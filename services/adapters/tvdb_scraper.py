# ==========================================
# SCRAPER DE THETVDB (BÚSQUEDA Y BIBLIOTECA)
# ==========================================
import json
import re
import asyncio
import tvdb_v4_official
from datetime import datetime
from core.logger import logger
from sqlmodel import Session, select, delete
from core.database import engine
from core.models.system import SystemConfig
from core.models.torrent import TorrentCache, TVDBCache, TorrentTVDBCandidates, TVDBEpisodes

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
# WRAPPERS ASÍNCRONOS PARA LA LIBRERÍA TVDB
# ==========================================

def _tvdb_search(api_key: str, query: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.search(query, type="series", limit=5)

def _tvdb_get_extended(api_key: str, tvdb_id: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_extended(tvdb_id)

def _tvdb_get_translations(api_key: str, tvdb_id: str, lang: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_translation(tvdb_id, lang)

def _tvdb_get_episodes_page(api_key: str, tvdb_id: str, page: int, lang: str):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_episodes(tvdb_id, page=page, season_type="default", lang=lang)

def _tvdb_get_episodes_page_default(api_key: str, tvdb_id: str, page: int):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_episodes(tvdb_id, page=page, season_type="default")


# ==========================================
# FLUJO 1: BÚSQUEDA Y CREACIÓN DE CANDIDATOS
# ==========================================

async def process_pending_tvdb():
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_is_enabled or not config.tvdb_api_key: return

        pending_torrents = session.exec(
            select(TorrentCache).where(TorrentCache.tvdb_status == "Pendiente").limit(5)
        ).all()
        if not pending_torrents: return
            
        for t in pending_torrents:
            query = clean_for_tvdb(t.original_title)
            if not query:
                t.tvdb_status = "Error"
                t.ai_status = "Manual"
                continue
                
            logger.info(f"🔎 [TVDB] Buscando candidatos para: '{query}'")
            try:
                results = await asyncio.to_thread(_tvdb_search, config.tvdb_api_key, query)
                if results:
                    for r in results:
                        tvdb_id_str = str(r.get("tvdb_id"))
                        existing_show = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id_str)).first()
                        
                        if not existing_show:
                            logger.info(f"⏳ [TVDB] Nuevo candidato ID {tvdb_id_str}. Obteniendo traducciones ES/EN...")
                            try: trans_spa = await asyncio.to_thread(_tvdb_get_translations, config.tvdb_api_key, tvdb_id_str, "spa")
                            except: trans_spa = {}
                            
                            try: trans_eng = await asyncio.to_thread(_tvdb_get_translations, config.tvdb_api_key, tvdb_id_str, "eng")
                            except: trans_eng = {}
                            
                            best_name = trans_eng.get("name") or trans_spa.get("name") or r.get("name", "Desconocido")
                            best_overview = trans_spa.get("overview") or trans_eng.get("overview") or r.get("overview", "Sin sinopsis")

                            raw_aliases = r.get("aliases", [])
                            clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
                            
                            new_show = TVDBCache(
                                tvdb_id=tvdb_id_str,
                                series_name_es=best_name,
                                aliases=json.dumps(clean_aliases, ensure_ascii=False),
                                overview_basic=best_overview,
                                poster_path=r.get("image_url"),
                                first_aired=r.get("year", "Desconocido"),
                                status=r.get("status", "Desconocido"),
                                is_full_record=False
                            )
                            session.add(new_show)
                            session.commit()
                        
                        link = session.exec(select(TorrentTVDBCandidates).where(
                            TorrentTVDBCandidates.torrent_guid == t.guid,
                            TorrentTVDBCandidates.tvdb_id == tvdb_id_str
                        )).first()
                        
                        if not link:
                            new_link = TorrentTVDBCandidates(torrent_guid=t.guid, tvdb_id=tvdb_id_str)
                            session.add(new_link)
                        
                    t.tvdb_status = "Candidatos"
                    logger.info(f"✅ [TVDB] Enlazados {len(results)} candidatos en base de datos para '{query}'.")
                else:
                    t.tvdb_status = "No Encontrado"
                    t.ai_status = "Manual"
                    logger.warning(f"⚠️ [TVDB] No se encontraron resultados para '{query}'.")
            except Exception as e:
                logger.error(f"❌ [TVDB] Error crítico buscando candidatos para '{query}': {e}")
                t.tvdb_status = "Error"
                t.ai_status = "Manual"
                
        session.commit()


# ==========================================
# FLUJO 2: CREACIÓN DE LA FICHA MAESTRA
# ==========================================

async def fetch_full_tvdb_series(tvdb_id: str, session: Session, config: SystemConfig):
    if not config.tvdb_api_key: return
    logger.info(f"⏳ [TVDB] Construyendo Ficha Maestra para el ID {tvdb_id}...")
    try:
        data = await asyncio.to_thread(_tvdb_get_extended, config.tvdb_api_key, tvdb_id)
        if not data: 
            logger.error(f"❌ [TVDB] La API no devolvió datos extendidos para el ID {tvdb_id}.")
            return
            
        try: translation_spa = await asyncio.to_thread(_tvdb_get_translations, config.tvdb_api_key, tvdb_id, "spa")
        except: translation_spa = {}
        try: translation_eng = await asyncio.to_thread(_tvdb_get_translations, config.tvdb_api_key, tvdb_id, "eng")
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

async def fetch_tvdb_episodes(tvdb_id: str, session: Session, config: SystemConfig):
    if not config.tvdb_api_key: return
    
    logger.info(f"📥 [TVDB] Iniciando descarga de episodios para ID {tvdb_id}...")
    try:
        page = 0
        has_more = True
        total_episodes = 0
        
        session.exec(delete(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == str(tvdb_id)))
        
        while has_more:
            logger.info(f"⏳ [TVDB] Descargando episodios (Página {page})...")
            
            try: data_spa = await asyncio.to_thread(_tvdb_get_episodes_page, config.tvdb_api_key, tvdb_id, page, "spa")
            except: data_spa = {}
            try: data_eng = await asyncio.to_thread(_tvdb_get_episodes_page, config.tvdb_api_key, tvdb_id, page, "eng")
            except: data_eng = {}
            try: data_orig = await asyncio.to_thread(_tvdb_get_episodes_page_default, config.tvdb_api_key, tvdb_id, page)
            except: data_orig = {}
                
            episodes_spa = data_spa.get("episodes", []) if data_spa else []
            episodes_eng = data_eng.get("episodes", []) if data_eng else []
            episodes_orig = data_orig.get("episodes", []) if data_orig else []
            
            if not episodes_spa and not episodes_orig:
                break
                
            dict_eng = {ep.get("id"): ep for ep in episodes_eng}
            dict_orig = {ep.get("id"): ep for ep in episodes_orig}
            
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
            
            page += 1
            if page > 20 or len(episodes_spa) < 100: 
                has_more = False
        
        session.commit()
        logger.info(f"✅ [TVDB] Guardados {total_episodes} episodios traducidos y formateados para la serie {tvdb_id}.")
        
    except Exception as e:
        logger.error(f"❌ [TVDB] Error crítico descargando episodios para ID {tvdb_id}: {e}")