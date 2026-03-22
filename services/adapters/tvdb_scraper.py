# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import asyncio
import json
import re
from datetime import datetime

import tvdb_v4_official
from sqlmodel import Session, delete, select

from core.database import engine
from core.logger import logger
from core.models.system import SystemConfig
from core.models.torrent import TorrentCache, TorrentTVDBCandidates, TVDBCache, TVDBEpisodes
from services.encrypt import decrypt_secret


# ==========================================
# PREPARACIÓN DE DATOS
# ==========================================

"""
Limpia el título original del tracker eliminando etiquetas entre corchetes, paréntesis 
y menciones de temporada para generar una cadena de búsqueda óptima en TheTVDB.
"""
def clean_for_tvdb(title: str) -> str:
    clean = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    clean = re.sub(r"(?i)\b(S\d{1,2}|Temporada\s*\d{1,2}|Season\s*\d{1,2})\b", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    clean = re.sub(r"^\s*-\s*|\s*-\s*$", "", clean)
    return clean.strip()


# ==========================================
# WRAPPERS PARA LA LIBRERÍA TVDB
# ==========================================

"""
Envía una consulta de búsqueda de series a la API de TheTVDB y devuelve 
los primeros 5 resultados encontrados.
"""
def _tvdb_search(api_key: str, query: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.search(query, type="series", limit=5)

"""
Obtiene los metadatos extendidos de una serie específica mediante su ID 
de TheTVDB (incluyendo temporadas y estado).
"""
def _tvdb_get_extended(api_key: str, tvdb_id: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_extended(tvdb_id)

"""
Recupera las traducciones oficiales (título y sinopsis) de una serie 
para un idioma específico (ej. 'spa', 'eng').
"""
def _tvdb_get_translations(api_key: str, tvdb_id: str, lang: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_translation(tvdb_id, lang)

"""
Obtiene una página específica de episodios de una serie en un idioma determinado.
Utilizado para paginar sobre series con muchas temporadas.
"""
def _tvdb_get_episodes_page(api_key: str, tvdb_id: str, page: int, lang: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_episodes(tvdb_id, page=page, season_type="default", lang=lang)

"""
Obtiene una página específica de episodios en el idioma original (por defecto).
Actúa como respaldo cuando no existen traducciones para ciertos episodios.
"""
def _tvdb_get_episodes_page_default(api_key: str, tvdb_id: str, page: int):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_episodes(tvdb_id, page=page, season_type="default")


# ==========================================
# FLUJO 1: BÚSQUEDA Y CREACIÓN DE CANDIDATOS
# ==========================================

"""
Trabajador de fondo que busca torrents recién añadidos y consulta TheTVDB 
descifrando la API Key para encontrar posibles coincidencias (candidatos). Guarda los resultados parciales 
en la base de datos para que la IA los evalúe posteriormente.
"""
async def process_pending_tvdb():
    torrents_to_process = []
    tvdb_api_key = None
    
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_is_enabled or not config.tvdb_api_key: return
        tvdb_api_key = decrypt_secret(config.tvdb_api_key)

        pending_torrents = session.exec(
            select(TorrentCache).where(TorrentCache.tvdb_status == "Pendiente").limit(5)
        ).all()
        
        if not pending_torrents: return
            
        for t in pending_torrents:
            torrents_to_process.append({
                "guid": t.guid,
                "original_title": t.original_title
            })

    for t_data in torrents_to_process:
        query = clean_for_tvdb(t_data["original_title"])
        if not query:
            with Session(engine) as session:
                t = session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                if t:
                    t.tvdb_status = "Error"
                    t.ai_status = "Manual"
                    session.commit()
            continue
            
        logger.info(f"🔎 [TVDB] Buscando candidatos para: '{query}'")
        try:
            results = await asyncio.to_thread(_tvdb_search, tvdb_api_key, query)
            candidates_to_save = []
            
            if results:
                for r in results:
                    tvdb_id_str = str(r.get("tvdb_id"))
                    
                    show_exists = False
                    with Session(engine) as check_session:
                        if check_session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id_str)).first():
                            show_exists = True
                            
                    if not show_exists:
                        logger.info(f"⏳ [TVDB] Nuevo candidato ID {tvdb_id_str}. Obteniendo traducciones ES/EN...")
                        try: trans_spa = await asyncio.to_thread(_tvdb_get_translations, tvdb_api_key, tvdb_id_str, "spa")
                        except: trans_spa = {}
                        
                        try: trans_eng = await asyncio.to_thread(_tvdb_get_translations, tvdb_api_key, tvdb_id_str, "eng")
                        except: trans_eng = {}
                        
                        candidates_to_save.append({
                            "raw_data": r, "trans_spa": trans_spa, "trans_eng": trans_eng, "exists": False
                        })
                    else:
                        candidates_to_save.append({
                            "raw_data": r, "exists": True
                        })

            with Session(engine) as session:
                try:
                    t = session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                    if not t: continue
                    
                    if results:
                        for cand in candidates_to_save:
                            r = cand["raw_data"]
                            tvdb_id_str = str(r.get("tvdb_id"))
                            
                            if not cand.get("exists"):
                                best_name = cand["trans_eng"].get("name") or cand["trans_spa"].get("name") or r.get("name", "Desconocido")
                                best_overview = cand["trans_spa"].get("overview") or cand["trans_eng"].get("overview") or r.get("overview", "Sin sinopsis")

                                raw_aliases = r.get("aliases", [])
                                clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
                                
                                if not session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id_str)).first():
                                    new_show = TVDBCache(
                                        tvdb_id=tvdb_id_str, series_name_es=best_name,
                                        aliases=json.dumps(clean_aliases, ensure_ascii=False),
                                        overview_basic=best_overview, poster_path=r.get("image_url"),
                                        first_aired=r.get("year", "Desconocido"), status=r.get("status", "Desconocido"),
                                        is_full_record=False
                                    )
                                    session.add(new_show)
                                    
                            link = session.exec(select(TorrentTVDBCandidates).where(
                                TorrentTVDBCandidates.torrent_guid == t_data["guid"],
                                TorrentTVDBCandidates.tvdb_id == tvdb_id_str
                            )).first()
                            
                            if not link:
                                new_link = TorrentTVDBCandidates(torrent_guid=t_data["guid"], tvdb_id=tvdb_id_str)
                                session.add(new_link)
                                
                        t.tvdb_status = "Candidatos"
                        session.commit()
                        logger.info(f"✅ [TVDB] Enlazados {len(results)} candidatos en base de datos para '{query}'.")
                    else:
                        t.tvdb_status = "No Encontrado"
                        t.ai_status = "Manual"
                        session.commit()
                        logger.warning(f"⚠️ [TVDB] No se encontraron resultados para '{query}'.")
                        
                except Exception as db_e:
                    session.rollback()
                    logger.error(f"⚠️ Error base de datos guardando candidatos para '{query}': {db_e}")
                    
        except Exception as e:
            logger.error(f"❌ [TVDB] Error crítico de red buscando candidatos para '{query}': {e}")
            with Session(engine) as error_session:
                try:
                    t = error_session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                    if t:
                        t.tvdb_status = "Error"
                        t.ai_status = "Manual"
                        error_session.commit()
                except Exception:
                    error_session.rollback()


# ==========================================
# FLUJO 2: CREACIÓN DE LA FICHA MAESTRA
# ==========================================

"""
Descarga la información completa (Ficha Maestra) de una serie en TheTVDB descifrando su API Key, 
incluyendo pósters, estado, fechas de emisión y traducciones, y la persiste 
definitivamente en la base de datos local.
"""
async def fetch_full_tvdb_series(tvdb_id: str, config: SystemConfig):
    if not config.tvdb_api_key: return
    tvdb_api_key = decrypt_secret(config.tvdb_api_key)
    
    logger.info(f"⏳ [TVDB] Construyendo Ficha Maestra para el ID {tvdb_id}...")
    try:
        data = await asyncio.to_thread(_tvdb_get_extended, tvdb_api_key, tvdb_id)
        if not data: 
            logger.error(f"❌ [TVDB] La API no devolvió datos extendidos para el ID {tvdb_id}.")
            return
            
        try: translation_spa = await asyncio.to_thread(_tvdb_get_translations, tvdb_api_key, tvdb_id, "spa")
        except: translation_spa = {}
        try: translation_eng = await asyncio.to_thread(_tvdb_get_translations, tvdb_api_key, tvdb_id, "eng")
        except: translation_eng = {}

        name_es = translation_eng.get("name") or translation_spa.get("name") or data.get("name", "Desconocido")
        overview_es = translation_spa.get("overview") or translation_eng.get("overview") or data.get("overview", "")
        
        seasons_dict = {}
        for s in data.get("seasons", []):
            if s.get("type", {}).get("id") == 1:
                s_num = str(s.get("number"))
                if s_num != "0": seasons_dict[s_num] = {"id": s.get("id"), "episodes": 0}

        raw_aliases = data.get("aliases", [])
        clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
        
        with Session(engine) as session:
            existing_show = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == str(tvdb_id))).first()
            
            if existing_show:
                existing_show.series_name_es = name_es
                existing_show.series_name_en = data.get("name")
                existing_show.aliases = json.dumps(clean_aliases, ensure_ascii=False)
                existing_show.overview_es = overview_es
                existing_show.overview_en = data.get("overview")
                existing_show.poster_path = data.get("image")
                existing_show.status = data.get("status", {}).get("name", "Desconocido")
                existing_show.first_aired = data.get("firstAired")
                existing_show.seasons_data = json.dumps(seasons_dict, ensure_ascii=False)
                existing_show.is_full_record = True
                existing_show.last_updated = datetime.utcnow()
                session.add(existing_show)
            else:
                new_entry = TVDBCache(
                    tvdb_id=str(tvdb_id), series_name_es=name_es, series_name_en=data.get("name"),
                    aliases=json.dumps(clean_aliases, ensure_ascii=False),
                    overview_es=overview_es, overview_en=data.get("overview"),
                    poster_path=data.get("image"), status=data.get("status", {}).get("name", "Desconocido"),
                    first_aired=data.get("firstAired"), seasons_data=json.dumps(seasons_dict, ensure_ascii=False),
                    is_full_record=True,
                    last_updated=datetime.utcnow()
                )
                session.add(new_entry)
                
            session.commit()
            
        logger.info(f"✅ [BIBLIOTECA TVDB] Ficha maestra guardada exitosamente: '{name_es}' (ID: {tvdb_id})")
 
    except Exception as e:
        logger.error(f"❌ [TVDB] Error crítico construyendo la ficha extendida para ID {tvdb_id}: {e}")


# ==========================================
# FLUJO 3: DESCARGA DE EPISODIOS MULTI-IDIOMA
# ==========================================

"""
Itera sobre todas las páginas de la API de TheTVDB para descargar la lista completa 
de episodios de una serie descifrando la clave de seguridad previamente. Combina metadatos en español e inglés para asegurar 
el mejor formato de renombrado (SxxEyy - Título).
"""
async def fetch_tvdb_episodes(tvdb_id: str, config: SystemConfig):
    if not config.tvdb_api_key: return
    tvdb_api_key = decrypt_secret(config.tvdb_api_key)
    
    logger.info(f"📥 [TVDB] Iniciando descarga de episodios para ID {tvdb_id}...")
    try:
        page = 0
        has_more = True
        total_episodes = 0
        
        with Session(engine) as session:
            session.exec(delete(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == str(tvdb_id)))
            session.commit()
        
        while has_more:
            logger.info(f"⏳ [TVDB] Descargando episodios (Página {page})...")
            
            try: data_spa = await asyncio.to_thread(_tvdb_get_episodes_page, tvdb_api_key, tvdb_id, page, "spa")
            except: data_spa = {}
            try: data_eng = await asyncio.to_thread(_tvdb_get_episodes_page, tvdb_api_key, tvdb_id, page, "eng")
            except: data_eng = {}
            try: data_orig = await asyncio.to_thread(_tvdb_get_episodes_page_default, tvdb_api_key, tvdb_id, page)
            except: data_orig = {}
                
            episodes_spa = data_spa.get("episodes", []) if data_spa else []
            episodes_eng = data_eng.get("episodes", []) if data_eng else []
            episodes_orig = data_orig.get("episodes", []) if data_orig else []
            
            if not episodes_spa and not episodes_orig:
                break
                
            dict_eng = {ep.get("id"): ep for ep in episodes_eng}
            dict_orig = {ep.get("id"): ep for ep in episodes_orig}
            
            with Session(engine) as session:
                for ep_spa in episodes_spa:
                    ep_id = ep_spa.get("id")
                    season_num = ep_spa.get("seasonNumber")
                    ep_num = ep_spa.get("number")
                    
                    if season_num is not None and ep_num is not None and int(season_num) > 0:
                        ep_en = dict_eng.get(ep_id, {})
                        ep_jp = dict_orig.get(ep_id, {})
                        
                        raw_name = (
                            ep_spa.get("name") or ep_spa.get("nameTranslated") or
                            ep_en.get("name") or ep_en.get("nameTranslated") or
                            ep_jp.get("name") or ep_jp.get("nameTranslated") or
                            f"Episodio {ep_num}"
                        )
                        
                        formatted_name = f"S{int(season_num):02d}E{int(ep_num):02d} - {raw_name}"
                        
                        new_ep = TVDBEpisodes(
                            tvdb_id=str(tvdb_id),
                            season_number=int(season_num),
                            episode_number=int(ep_num),
                            name_es=formatted_name,
                            air_date=ep_spa.get("aired") or ep_jp.get("aired")
                        )
                        session.add(new_ep)
                        total_episodes += 1
                session.commit()
            
            page += 1
            if page > 20 or len(episodes_spa) < 100: 
                has_more = False
                
            await asyncio.sleep(0.1)
        
        logger.info(f"✅ [TVDB] Guardados {total_episodes} episodios traducidos y formateados para la serie {tvdb_id}.")
        
    except Exception as e:
        logger.error(f"❌ [TVDB] Error crítico descargando episodios para ID {tvdb_id}: {e}")