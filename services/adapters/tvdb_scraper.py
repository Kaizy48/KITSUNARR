# ==========================================
# SCRAPER DE THETVDB (BÚSQUEDA Y BIBLIOTECA)
# ==========================================
import json
import re
import asyncio
import tvdb_v4_official
from datetime import datetime
from core.logger import logger
from sqlmodel import Session, select
from core.database import engine
from core.models.system import SystemConfig
from core.models.torrent import TorrentCache, TVDBCache, TorrentTVDBCandidates, TVDBEpisodes
from sqlmodel import Session, select, delete

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
# WRAPPERS SINCRONOS PARA LA LIBRERÍA TVDB
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


# ==========================================
# FLUJO 1: BÚSQUEDA Y CREACIÓN DE CANDIDATOS
# ==========================================

"""
Procesa los torrents pendientes buscando coincidencias (candidatos) en TheTVDB.
Ahora utiliza un modelo relacional en lugar de JSONs, creando "Fichas Básicas" 
en TVDBCache y enlazándolas al Torrent mediante TorrentTVDBCandidates.
"""
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
                
            logger.info(f"🔎 Buscando en TVDB: '{query}'")
            try:
                results = await asyncio.to_thread(_tvdb_search, config.tvdb_api_key, query)
                if results:
                    for r in results:
                        tvdb_id_str = str(r.get("tvdb_id"))
                        
                        existing_show = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id_str)).first()
                        
                        if not existing_show:
                            raw_aliases = r.get("aliases", [])
                            clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
                            
                            new_show = TVDBCache(
                                tvdb_id=tvdb_id_str,
                                series_name_es=r.get("name", "Desconocido"),
                                aliases=json.dumps(clean_aliases, ensure_ascii=False),
                                overview_basic=r.get("overview", "Sin sinopsis"),
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
                    logger.info(f"✅ Enlazados {len(results)} candidatos en base de datos para '{query}'.")
                else:
                    t.tvdb_status = "No Encontrado"
                    t.ai_status = "Manual"
                    logger.info(f"⚠️ TVDB no devolvió resultados para '{query}'.")
            except Exception as e:
                logger.error(f"❌ Error buscando en TVDB: {e}")
                t.tvdb_status = "Error"
                t.ai_status = "Manual"
                
        session.commit()


# ==========================================
# FLUJO 2: CREACIÓN DE LA FICHA MAESTRA
# ==========================================

"""
Descarga toda la información detallada de una serie confirmada y actualiza
la Ficha Básica transformándola en una Ficha Maestra (is_full_record=True).
"""
async def fetch_full_tvdb_series(tvdb_id: str, session: Session, config: SystemConfig):
    if not config.tvdb_api_key: return
    try:
        data = await asyncio.to_thread(_tvdb_get_extended, config.tvdb_api_key, tvdb_id)
        if not data: return
            
        try: translation_spa = await asyncio.to_thread(_tvdb_get_translations, config.tvdb_api_key, tvdb_id, "spa")
        except: translation_spa = {}

        name_es = translation_spa.get("name") if translation_spa.get("name") else data.get("name", "Desconocido")
        overview_es = translation_spa.get("overview") if translation_spa.get("overview") else data.get("overview", "")
        
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
        logger.info(f"📚 [BIBLIOTECA TVDB] Ficha maestra creada/actualizada: '{name_es}' (ID: {tvdb_id})")
 
    except Exception as e:
        logger.error(f"❌ Error construyendo la ficha extendida para ID {tvdb_id}: {e}")
        
def _tvdb_get_episodes_page(api_key: str, tvdb_id: str, page: int):
    tvdb = tvdb_v4_official.TVDB(api_key)
    return tvdb.get_series_episodes(tvdb_id, page=page, season_type="default")

"""
Descarga todos los episodios de una serie iterando por las páginas de la API
y los almacena en la tabla relacional TVDBEpisodes.
"""
async def fetch_tvdb_episodes(tvdb_id: str, session: Session, config: SystemConfig):
    if not config.tvdb_api_key: return
    
    logger.info(f"📥 Descargando capítulos para la serie ID {tvdb_id}...")
    try:
        page = 0
        has_more = True
        total_episodes = 0
        session.exec(delete(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == str(tvdb_id)))
        
        while has_more:
            data = await asyncio.to_thread(_tvdb_get_episodes_page, config.tvdb_api_key, tvdb_id, page)
            if not data or "episodes" not in data:
                break
                
            episodes_list = data["episodes"]
            if not episodes_list:
                break
                
            for ep in episodes_list:
                season_num = ep.get("seasonNumber")
                ep_num = ep.get("number")
                
                if season_num is not None and ep_num is not None and int(season_num) > 0:
                    new_ep = TVDBEpisodes(
                        tvdb_id=str(tvdb_id),
                        season_number=int(season_num),
                        episode_number=int(ep_num),
                        name_es=ep.get("name") or ep.get("nameTranslated") or f"Episodio {ep_num}",
                        air_date=ep.get("aired")
                    )
                    session.add(new_ep)
                    total_episodes += 1
            
            page += 1
            if page > 20 or len(episodes_list) < 100: 
                has_more = False
        
        session.commit()
        logger.info(f"✅ Guardados {total_episodes} episodios en la base de datos para la serie {tvdb_id}.")
        
    except Exception as e:
        logger.error(f"❌ Error descargando episodios para ID {tvdb_id}: {e}")