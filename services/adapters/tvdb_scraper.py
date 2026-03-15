# ==========================================
# SCRAPER DE THETVDB (BÚSQUEDA DE CANDIDATOS)
# ==========================================
import httpx
import json
import re
from core.logger import logger
from sqlmodel import Session, select
from core.database import engine
from core.models.torrent import TorrentCache
from core.models.system import SystemConfig

BASE_URL = "https://api4.thetvdb.com/v4"

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

"""
Obtiene o renueva un token JWT de sesión temporal para autenticarse contra la API v4 de TheTVDB.
Almacena el token en la base de datos para persistencia entre reinicios del contenedor.

Parámetros:
    session (Session): Sesión activa de base de datos.
    config (SystemConfig): Objeto de configuración con la clave API de TheTVDB.
    force_refresh (bool): Si es True, fuerza la generación de un nuevo token ignorando la caché.

Retorna:
    str | None: El token JWT válido o None si la autenticación falla.
"""
async def get_tvdb_token(session: Session, config: SystemConfig, force_refresh: bool = False) -> str | None:
    if not force_refresh and config.tvdb_token:
        return config.tvdb_token
        
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{BASE_URL}/login", json={"apikey": config.tvdb_api_key})
            resp.raise_for_status()
            
            token = resp.json().get("data", {}).get("token")
            if token:
                config.tvdb_token = token
                session.commit()
                return token
    except Exception as e:
        logger.error(f"❌ Error obteniendo token de TVDB: {e}")
        
    return None

"""
Procesa de forma automatizada los torrents pendientes de vinculación buscando 
posibles coincidencias (candidatos) en la base de datos de TheTVDB.
Almacena un JSON con los resultados en la caché del torrent para su posterior uso por la IA.
"""
async def process_pending_tvdb():
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        
        if not config or not config.tvdb_is_enabled or not config.tvdb_api_key:
            return

        pending_torrents = session.exec(
            select(TorrentCache).where(TorrentCache.tvdb_status == "Pendiente").limit(5)
        ).all()
        
        if not pending_torrents:
            return
            
        token = await get_tvdb_token(session, config)
        if not token:
            logger.error("⚠️ TVDB habilitado pero no se pudo obtener token.")
            return

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json","Accept-Language": "spa"}
        
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            for t in pending_torrents:
                query = clean_for_tvdb(t.original_title)
                
                if not query:
                    t.tvdb_status = "Error"
                    continue
                    
                logger.info(f"🔎 Buscando en TVDB: '{query}'")
                
                try:
                    resp = await client.get(f"{BASE_URL}/search", params={"query": query, "type": "series", "limit": 3})
                    
                    if resp.status_code == 401:
                        logger.warning("🔄 Token TVDB expirado. Renovando...")
                        token = await get_tvdb_token(session, config, force_refresh=True)
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                            resp = await client.get(f"{BASE_URL}/search", params={"query": query, "type": "series", "limit": 3})
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        results = data.get("data", [])
                        
                        if results:
                            candidates = []
                            for r in results:
                                aliases = r.get("aliases", [])
                                
                                candidates.append({
                                "tvdb_id": r.get("tvdb_id"),
                                "name": r.get("name"),
                                "aliases": aliases, # <--- NUEVO
                                "year": r.get("year", "Desconocido"),
                                "status": r.get("status", "Desconocido"),
                                "overview": r.get("overview", "Sin sinopsis")
                            })
                                
                            t.tvdb_candidates = json.dumps(candidates, ensure_ascii=False)
                            t.tvdb_status = "Candidatos"
                            logger.info(f"✅ Encontrados {len(candidates)} candidatos en TVDB para '{query}'.")
                        else:
                            t.tvdb_status = "No Encontrado"
                            logger.info(f"⚠️ TVDB no devolvió resultados para '{query}'.")
                            
                    else:
                        t.tvdb_status = "Error"
                        logger.error(f"❌ Error de la API de TVDB al buscar '{query}': {resp.status_code}")
                        
                except Exception as e:
                    logger.error(f"❌ Error de red buscando en TVDB: {e}")
                    t.tvdb_status = "Error"
                    
            session.commit()