import re
import unicodedata
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import SystemConfig, TorrentCache, IndexerConfig
from core.app.encrypt import MASTER_KEY, decrypt_secret
from core.app.logger import logger
from core.app.indexers.manager import indexer_manager
from core.app.indexers.unionfansub import TorrentDownloadError
from services.torrent.metainfo import calculate_metainfo_hash

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
# Construye un nombre seguro para el adjunto desde título y GUID. Devuelve el
# par (Unicode visible, fallback ASCII) siempre terminado en .torrent; no hace
# I/O ni muta entradas. Elimina controles, rutas, caracteres de cabecera y
# limita longitud; entradas vacías usan el GUID. Depende de re/unicodedata y su
# salida alimenta _build_content_disposition.
# ------------------------------------------------------------
def _build_download_filename(title: str, guid: str) -> tuple[str, str]:
    raw_title = str(title or "").strip()
    if raw_title.lower().endswith(".torrent"):
        raw_title = raw_title[:-8]

    cleaned_chars = []
    for character in raw_title:
        if character in "/\\":
            cleaned_chars.append("_")
        elif unicodedata.category(character).startswith("C"):
            continue
        elif character in '\"<>:|?*':
            cleaned_chars.append("_")
        else:
            cleaned_chars.append(character)
    unicode_stem = re.sub(r"\s+", " ", "".join(cleaned_chars)).strip(" ._")[:180]
    if not unicode_stem:
        unicode_stem = str(guid or "torrent").strip()[:80] or "torrent"

    ascii_stem = (
        unicodedata.normalize("NFKD", unicode_stem)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_stem = re.sub(r"[^A-Za-z0-9._()\[\] -]+", "_", ascii_stem)
    ascii_stem = re.sub(r"\s+", " ", ascii_stem).strip(" ._")[:180]
    if not ascii_stem:
        ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(guid or "torrent"))[:80]
    ascii_stem = ascii_stem or "torrent"
    return f"{unicode_stem}.torrent", f"{ascii_stem}.torrent"


# ------------------------------------------------------------
# Codifica título/GUID como Content-Disposition RFC 6266 y devuelve una cabecera
# completamente ASCII: filename conserva compatibilidad y filename* transporta
# UTF-8. No hace I/O ni modifica estado; quote puede propagar errores inesperados
# de una cadena corrupta. Depende de _build_download_filename y urllib.parse.quote.
# ------------------------------------------------------------
def _build_content_disposition(title: str, guid: str) -> str:
    unicode_filename, ascii_filename = _build_download_filename(title, guid)
    encoded_filename = quote(unicode_filename, safe="")
    return (
        f'attachment; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )

# ------------------------------------------------------------
# Endpoint de descarga de Kitsunarr. Recibe GUID/sufijo/request/API key y devuelve
# Response de metainfo para Sonarr o navegador. Lee autenticación/ficha/cookie en
# una sesión corta, la cierra antes del I/O remoto, actualiza el hash en otra
# transacción y genera filename ASCII+UTF-8 seguro. Responde 401/404/500/502 según
# frontera y no expone secretos; depende del manager, download_torrent,
# calculate_metainfo_hash y _build_content_disposition. El sufijo se conserva
# solo por compatibilidad URL.
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
        download_title = db_torrent.original_title or db_torrent.enriched_title or guid

    try:
        torrent_bytes = await indexer.download_torrent(source_guid, tracker_cookie)
    except Exception as exc:
        if isinstance(exc, TorrentDownloadError):
            diagnostic = str(exc)
        else:
            diagnostic = type(exc).__name__
        logger.error(
            f"Error descargando desde {indexer.name} para GUID {guid} "
            f"(source_guid={source_guid}, tipo={type(exc).__name__}, detalle={diagnostic})."
        )
        raise HTTPException(
            status_code=502,
            detail="Error de comunicación con el tracker origen.",
        ) from exc

    try:
        info_hash = calculate_metainfo_hash(torrent_bytes)

        with Session(engine) as session:
            stored_torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == guid)
            ).first()
            if stored_torrent:
                stored_torrent.info_hash = info_hash
                session.add(stored_torrent)
                session.commit()
        logger.info(f"✅ Info Hash calculado para {guid}: {info_hash}")
    except Exception as exc:
        logger.error(
            f"⚠️ No se pudo calcular Info Hash para {guid}: {type(exc).__name__}."
        )

    content_disposition = _build_content_disposition(download_title, guid)
    return Response(
        content=torrent_bytes,
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": content_disposition},
    )
