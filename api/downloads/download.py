import hashlib
import bencodepy
import jwt
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import SystemConfig, TorrentCache, IndexerConfig
from core.app.encrypt import MASTER_KEY, decrypt_secret
from core.app.logger import logger
from core.app.indexers.manager import indexer_manager

router = APIRouter(tags=["Downloads"])

# ------------------------------------------------------------
# Resuelve el identificador real del torrent en el tracker origen
# cuando llega una petición de descarga desde Sonarr, la UI o un
# enlace generado por versiones anteriores de Kitsunarr.
# ------------------------------------------------------------
def _resolve_source_guid(db_torrent: TorrentCache, guid: str) -> str:
    if db_torrent and db_torrent.source_guid:
        return db_torrent.source_guid
    if "-" in guid:
        return guid.split("-", 1)[1]
    return guid

# ------------------------------------------------------------
# Endpoint de descarga de Kitsunarr. Valida el acceso por API key
# o sesión web, solicita el archivo .torrent al indexador origen,
# guarda el info hash cuando puede calcularlo y entrega el torrent
# a Sonarr o al navegador.
# ------------------------------------------------------------
@router.get("/api/download/{guid}_{suffix}")
async def proxy_download_torrent(guid: str, suffix: str, request: Request, apikey: str = Query(None)):
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        is_authenticated = False

        if apikey and sys_config:
            stored_api_key = sys_config.api_key
            plain_api_key = decrypt_secret(stored_api_key)
            if apikey == stored_api_key or apikey == plain_api_key:
                is_authenticated = True

        if not is_authenticated:
            token = request.cookies.get("kitsunarr_session")
            if token:
                try:
                    jwt.decode(token, MASTER_KEY, algorithms=["HS256"])
                    is_authenticated = True
                except Exception:
                    pass

        if not is_authenticated:
            logger.warning(f"🔒 Bloqueado intento de descarga no autorizado para GUID: {guid}")
            raise HTTPException(status_code=401, detail="Acceso denegado. Se requiere API Key o Sesión válida.")

        db_torrent = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not db_torrent:
            raise HTTPException(status_code=404, detail="Torrent no encontrado en la caché local.")

        indexer = indexer_manager.get_indexer(db_torrent.indexer)
        if not indexer:
            raise HTTPException(status_code=500, detail=f"El indexador '{db_torrent.indexer}' no está disponible.")

        idx_config = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == db_torrent.indexer)).first()
        tracker_cookie = decrypt_secret(idx_config.cookie_string) if idx_config and idx_config.cookie_string else ""

        source_guid = _resolve_source_guid(db_torrent, guid)

        try:
            torrent_bytes = await indexer.download_torrent(source_guid, tracker_cookie)
        except Exception as e:
            logger.error(f"Error descargando desde {indexer.name}: {e}")
            raise HTTPException(status_code=502, detail="Error de comunicación con el tracker origen.")

        try:
            torrent_data = bencodepy.decode(torrent_bytes)
            info_dict = torrent_data[b"info"]
            info_encoded = bencodepy.encode(info_dict)
            info_hash = hashlib.sha1(info_encoded).hexdigest().lower()
            
            db_torrent.info_hash = info_hash
            session.commit()
            logger.info(f"✅ Info Hash calculado para {guid}: {info_hash}")
        except Exception as e:
            logger.error(f"⚠️ No se pudo calcular Info Hash para {guid}: {e}")

        filename = f"{db_torrent.original_title}.torrent".replace("/", "_").replace("\\", "_")
        
        return Response(
            content=torrent_bytes,
            media_type="application/x-bittorrent",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
