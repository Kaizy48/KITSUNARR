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
from core.models.torrent import TorrentCache, TVDBCache
from core.models.system import SystemConfig

"""
Limpia el título original del tracker eliminando etiquetas entre corchetes, paréntesis 
y menciones de temporada para generar una cadena de búsqueda óptima en TheTVDB.

Parámetros:
    title (str): Título original extraído del torrent.

Retorna:
    str: Título limpio preparado para la API.
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
# La librería oficial usa 'requests' (síncrono). Envolvemos las llamadas 
# para ejecutarlas en hilos y no bloquear el event loop de FastAPI.

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
# FLUJO 1: BÚSQUEDA DE CANDIDATOS
# ==========================================

"""
Procesa de forma automatizada los torrents pendientes de vinculación buscando 
posibles coincidencias (candidatos) en TheTVDB.
Almacena un JSON con los resultados en la caché del torrent para su posterior uso por la IA.
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
                    candidates = []
                    for r in results:
                        raw_aliases = r.get("aliases", [])
                        clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
                        
                        candidates.append({
                            "tvdb_id": str(r.get("tvdb_id")),
                            "name": r.get("name"),
                            "aliases": clean_aliases, 
                            "year": r.get("year", "Desconocido"),
                            "status": r.get("status", "Desconocido"),
                            "overview": r.get("overview", "Sin sinopsis"),
                            "image_url": r.get("image_url")
                        })
                        
                    t.tvdb_candidates = json.dumps(candidates, ensure_ascii=False)
                    t.tvdb_status = "Candidatos"
                    logger.info(f"✅ Encontrados {len(candidates)} candidatos.")
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
Descarga toda la información detallada de una serie confirmada y la guarda 
en la tabla TVDBCache para construir la biblioteca local persistente.

Parámetros:
    tvdb_id (str): Identificador oficial de la serie.
    session (Session): Sesión activa de base de datos.
    config (SystemConfig): Configuración con la API Key.
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

        new_entry = TVDBCache(
            tvdb_id=str(tvdb_id), series_name_es=name_es, series_name_en=data.get("name"),
            aliases=json.dumps(clean_aliases, ensure_ascii=False),
            overview_es=overview_es, overview_en=data.get("overview"),
            poster_path=data.get("image"), status=data.get("status", {}).get("name", "Desconocido"),
            first_aired=data.get("firstAired"), seasons_data=json.dumps(seasons_dict, ensure_ascii=False),
            last_updated=datetime.utcnow()
        )
        session.merge(new_entry)
        session.commit()
        
        logger.info(f"📚 [BIBLIOTECA TVDB] Ficha maestra creada/actualizada: '{name_es}' (ID: {tvdb_id})")
        
    except Exception as e:
        logger.error(f"❌ Error construyendo la ficha extendida para ID {tvdb_id}: {e}")