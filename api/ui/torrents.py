import hashlib
import asyncio
import httpx
import bencodepy
from fastapi import APIRouter, Request, Response, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from sqlmodel import Session, select, desc, or_
import json
from datetime import datetime

from core.database.engine import engine
from core.database.models import SystemConfig, TorrentCache, TVDBCache, IndexerConfig
from core.app.logger import logger
from core.app.background import wake_worker
from core.app.encrypt import decrypt_secret
from core.app.indexers.manager import indexer_manager
from services.torrent.qbittorrent import get_all_unionfansub_torrents, get_torrent_telemetry, qbittorrent_login
from services.tvdb.tvdb_api import fetch_full_tvdb_series
from core.app.export import export_torrents_only, export_tvdb_only, export_full_bundle, import_relational_data

router = APIRouter(prefix="/api/ui", tags=["UI Torrents"])

DEFAULT_POSTER_PATH = "static/img/Kitsunarr-logo-512x512.png"
PLACEHOLDER_POSTER_PATTERNS = (
    "noimage",
    "no-image",
    "no_image",
    "noimg",
    "nopic",
    "no-pic",
    "placeholder",
    "default",
    "sinimagen",
    "sin-imagen",
    "sin_imagen",
    "sinportada",
    "sin-portada",
    "sin_portada",
    "nocover",
    "no-cover",
    "no_cover",
    "noposter",
    "no-poster",
    "no_poster",
    "missing",
    "images/smilies/smile.png",
)

# ------------------------------------------------------------
# Devuelve la imagen local de Kitsunarr cuando un torrent no tiene
# póster útil o el proxy de imagen no puede resolverlo.
# ------------------------------------------------------------
def _poster_fallback_response() -> FileResponse:
    return FileResponse(DEFAULT_POSTER_PATH, media_type="image/png")

# ------------------------------------------------------------
# Detecta pósters genéricos del tracker para evitar mostrar imágenes
# vacías o de marcador en la biblioteca.
# ------------------------------------------------------------
def _is_placeholder_poster_url(url: str) -> bool:
    if not url:
        return True
    lowered = url.lower()
    if "unionfansub" not in lowered:
        return False
    return any(pattern in lowered for pattern in PLACEHOLDER_POSTER_PATTERNS)

# ------------------------------------------------------------
# Datos editables de una ficha torrent en la caché local de
# Kitsunarr.
# ------------------------------------------------------------
class CacheEditForm(BaseModel):
    ai_translated_title: str
    description: Optional[str] = None
    tvdb_id: Optional[str] = None
    parsed_season: Optional[int] = None
    is_batch: bool = False
    tags: Optional[str] = None
    rename_mapping: Optional[str] = None

# ------------------------------------------------------------
# Prepara una ficha torrent para la UI añadiendo estados derivados
# como freeleech activo.
# ------------------------------------------------------------
def _enrich_torrent_for_ui(t: TorrentCache) -> dict:
    item = t.dict()
    item["is_freeleech"] = False
    if t.freeleech_until and t.freeleech_until > datetime.utcnow():
        item["is_freeleech"] = True
    return item


# ------------------------------------------------------------
# Inicia sesión en qBittorrent usando la configuración guardada para
# que Kitsunarr pueda consultar torrents vinculados.
# ------------------------------------------------------------
async def _login_qbittorrent_from_config(session: Session) -> tuple[Optional[SystemConfig], Optional[str], Optional[str]]:
    config = session.exec(select(SystemConfig)).first()
    if not config or not config.qbittorrent_url:
        return config, None, "qBittorrent no estÃ¡ configurado."
    if not config.qbittorrent_user or not config.qbittorrent_password:
        return config, None, "Faltan usuario o contraseÃ±a de qBittorrent."

    password = decrypt_secret(config.qbittorrent_password)
    sid = await qbittorrent_login(config.qbittorrent_url, config.qbittorrent_user, password)
    if not sid:
        return config, None, "No se pudo iniciar sesiÃ³n en qBittorrent."
    return config, sid, None


# ------------------------------------------------------------
# Copia la telemetría de qBittorrent sobre la ficha local para que
# la UI muestre estado, progreso, velocidades y pares.
# ------------------------------------------------------------
def _apply_telemetry_to_torrent(torrent: TorrentCache, telemetry: dict | None) -> None:
    if telemetry:
        torrent.exists_in_client = True
        torrent.client_status = telemetry.get("client_status", "unknown")
        torrent.progress = float(telemetry.get("progress") or 0.0)
        torrent.peers_seeds = int(telemetry.get("peers_seeds") or 0)
        torrent.peers_leechs = int(telemetry.get("peers_leechs") or 0)
        torrent.download_speed = int(telemetry.get("download_speed") or 0)
        torrent.upload_speed = int(telemetry.get("upload_speed") or 0)
        torrent.eta = int(telemetry.get("eta") or 0)
    else:
        torrent.exists_in_client = False
        torrent.client_status = "not_found"
        torrent.progress = 0.0
        torrent.download_speed = 0
        torrent.upload_speed = 0
        torrent.eta = 8640000

# ------------------------------------------------------------
# Devuelve la galería principal de caché separando torrents sin
# vincular, series TVDB vinculadas y una lista plana para laboratorios.
# ------------------------------------------------------------
@router.get("/cache")
async def get_torrent_cache():
    with Session(engine) as session:
        unlinked_torrents_db = session.exec(
            select(TorrentCache).where(
                (TorrentCache.tvdb_id == None) | (TorrentCache.tvdb_status != "Listo")
            ).order_by(desc(TorrentCache.pub_date)).limit(1000)
        ).all()

        linked_tvdb_rows = session.exec(
            select(TorrentCache.tvdb_id).where(
                TorrentCache.tvdb_id != None, TorrentCache.tvdb_status == "Listo"
            )
        ).all()

        linked_counts = {}
        for tvdb_id in linked_tvdb_rows:
            if not tvdb_id:
                continue
            linked_counts[tvdb_id] = linked_counts.get(tvdb_id, 0) + 1

        linked_tvdb_ids = list(linked_counts.keys())

        linked_series = []
        if linked_tvdb_ids:
            series_db = session.exec(
                select(TVDBCache).where(TVDBCache.tvdb_id.in_(linked_tvdb_ids))
            ).all()
            for s in series_db:
                s_item = s.dict()
                s_item["linked_torrents_count"] = linked_counts.get(s.tvdb_id, 0)
                linked_series.append(s_item)

        all_torrents_db = session.exec(
            select(TorrentCache).order_by(desc(TorrentCache.pub_date)).limit(2000)
        ).all()

        results_unlinked = [_enrich_torrent_for_ui(t) for t in unlinked_torrents_db]
        all_torrents = [_enrich_torrent_for_ui(t) for t in all_torrents_db]

        return {
            "success": True,
            "unlinked_torrents": results_unlinked,
            "linked_series": linked_series,
            "torrents": all_torrents,
        }

# ------------------------------------------------------------
# Devuelve la vista de una serie TVDB con todos los torrents locales
# validados que Kitsunarr tiene vinculados a ella.
# ------------------------------------------------------------
@router.get("/cache/series/{tvdb_id}")
async def get_series_shelf(tvdb_id: str):
    with Session(engine) as session:
        series = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id)).first()
        if not series:
            return {"success": False, "error": "Serie no encontrada"}

        torrents = session.exec(
            select(TorrentCache).where(
                TorrentCache.tvdb_id == tvdb_id, TorrentCache.tvdb_status == "Listo"
            ).order_by(desc(TorrentCache.pub_date))
        ).all()

        return {
            "success": True,
            "series": series.dict(),
            "torrents": [_enrich_torrent_for_ui(t) for t in torrents]
        }

# ------------------------------------------------------------
# Devuelve la ficha técnica de un torrent y, si está vinculado con
# qBittorrent, refresca su telemetría antes de responder.
# ------------------------------------------------------------
@router.get("/cache/torrent/{guid}")
async def get_torrent_detail(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado"}

        telemetry_ratio = None
        if t.info_hash:
            sys_config, sid, qb_error = await _login_qbittorrent_from_config(session)
            if sid and sys_config:
                telemetry = await get_torrent_telemetry(sys_config.qbittorrent_url, sid, t.info_hash)
                telemetry_ratio = telemetry.get("ratio") if telemetry else None
                _apply_telemetry_to_torrent(t, telemetry)
                session.add(t)
                session.commit()
                session.refresh(t)

        item = _enrich_torrent_for_ui(t)
        item["ratio"] = telemetry_ratio
        if t.tvdb_id:
            series = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == t.tvdb_id)).first()
            if series:
                item["tvdb_series_name_es"] = series.series_name_es

        return {"success": True, "torrent": item}

# ------------------------------------------------------------
# Actualiza manualmente una ficha de la caché con título IA,
# descripción, temporada, tags, renombrado y vínculo TVDB.
# ------------------------------------------------------------
@router.put("/cache/{guid}")
async def update_cache_entry(guid: str, data: CacheEditForm):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False}

        linked_tvdb_id = None

        t.ai_translated_title = data.ai_translated_title
        t.description = data.description
        t.parsed_season = data.parsed_season
        t.is_batch = data.is_batch

        if data.tags is not None:
            t.tags = data.tags
        if data.rename_mapping is not None:
            t.rename_mapping = data.rename_mapping

        if data.tvdb_id:
            t.tvdb_id = data.tvdb_id
            t.tvdb_status = "Listo"
            linked_tvdb_id = data.tvdb_id

        session.commit()

    if linked_tvdb_id:
        asyncio.create_task(fetch_full_tvdb_series(linked_tvdb_id))

    return {"success": True}

# ------------------------------------------------------------
# Elimina una ficha torrent de la caché local de Kitsunarr.
# ------------------------------------------------------------
@router.delete("/cache/{guid}")
async def delete_cache_entry(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if t:
            logger.info(
                f"🗑️ [CACHE] Eliminando ficha torrent: ID={t.guid} | Título='{(t.original_title or 'Sin título').strip()}' | TVDB={t.tvdb_id or 'Sin vincular'}"
            )
            session.delete(t)
            session.commit()
            logger.info(f"✅ [CACHE] Ficha torrent eliminada: ID={guid}")
            return {"success": True}
    logger.warning(f"⚠️ [CACHE] Intento de borrar ficha torrent inexistente: ID={guid}")
    return {"success": False}

# ------------------------------------------------------------
# Ejecuta una búsqueda interactiva en los indexadores activos,
# sincroniza la caché local y despierta el worker de enriquecido.
# ------------------------------------------------------------
@router.get("/search/interactive")
@router.get("/search")
async def interactive_ui_search(q: str, request: Request):
    logger.info(f"🔎 [SEARCH] Búsqueda iniciada interactivamente: '{q}'")

    with Session(engine) as session:
        active_indexers = session.exec(
            select(IndexerConfig).where(
                or_(IndexerConfig.is_enabled == True, IndexerConfig.is_enabled == None)
            )
        ).all()

        if not active_indexers:
            configured_indexers = session.exec(select(IndexerConfig)).all()
            if configured_indexers:
                return {
                    "success": False,
                    "error": "Hay indexadores configurados, pero están deshabilitados. Actívalos en la vista de Indexadores."
                }
            return {"success": False, "error": "No hay indexadores configurados todavía."}

        base_url = str(request.base_url).rstrip("/")
        all_results = []

        for idx in active_indexers:
            indexer = indexer_manager.get_indexer(idx.identifier)
            if not indexer:
                continue

            cookie = decrypt_secret(idx.cookie_string) if idx.cookie_string else ""
            try:
                results = await indexer.search(q, cookie)

                result_guids = [str(r.get("guid")) for r in results if r.get("guid")]
                existing_by_guid = {}
                if result_guids:
                    existing_rows = session.exec(
                        select(TorrentCache).where(TorrentCache.guid.in_(result_guids))
                    ).all()
                    existing_by_guid = {row.guid: row for row in existing_rows}

                for r in results:
                    guid = r.get("guid")
                    if not guid:
                        continue

                    db_t = existing_by_guid.get(guid)
                    title_for_log = (r.get("original_title") or r.get("title") or f"Torrent {guid}").strip()
                    logger.info(
                        f"📦 [CACHE] [UI] {'HIT' if db_t else 'MISS'} | ID={guid} | Título='{title_for_log}'"
                    )

                    if not db_t:
                        db_t = TorrentCache(
                            guid=guid,
                            source_guid=r.get("source_guid"),
                            original_title=r.get("original_title") or r.get("title"),
                            enriched_title=r.get("title"),
                            description=r.get("description"),
                            poster_url=r.get("poster_url"),
                            fansub_name=r.get("fansub") or idx.name,
                            indexer=idx.identifier,
                            download_url=f"{base_url}/api/download/{guid}_base",
                            pub_date=r.get("publish_date"),
                            size_bytes=r.get("size_bytes", 0),
                            raw_filenames=r.get("raw_filenames"),
                            tags=r.get("tags")
                        )
                        session.add(db_t)
                        session.commit()
                        session.refresh(db_t)
                        existing_by_guid[guid] = db_t
                    else:
                        updated = False
                        if r.get("source_guid") and db_t.source_guid != r.get("source_guid"):
                            db_t.source_guid = r.get("source_guid")
                            updated = True
                        if r.get("poster_url") and db_t.poster_url != r.get("poster_url"):
                            db_t.poster_url = r.get("poster_url")
                            updated = True
                        if r.get("description") and not db_t.description:
                            db_t.description = r.get("description")
                            updated = True
                        if r.get("title") and db_t.enriched_title != r.get("title"):
                            db_t.enriched_title = r.get("title")
                            updated = True
                        if r.get("original_title") and db_t.original_title != r.get("original_title"):
                            db_t.original_title = r.get("original_title")
                            updated = True
                        if r.get("raw_filenames") and not db_t.raw_filenames:
                            db_t.raw_filenames = r.get("raw_filenames")
                            updated = True
                        if r.get("tags") and not db_t.tags:
                            db_t.tags = r.get("tags")
                            updated = True
                        if r.get("publish_date") and not db_t.pub_date:
                            db_t.pub_date = r.get("publish_date")
                            updated = True
                        incoming_fansub = r.get("fansub") or idx.name
                        if incoming_fansub and db_t.fansub_name != incoming_fansub:
                            db_t.fansub_name = incoming_fansub
                            updated = True
                        if updated:
                            session.add(db_t)
                            session.commit()
                            session.refresh(db_t)

                    item = _enrich_torrent_for_ui(db_t)

                    if db_t.tvdb_id:
                        tvdb_series = session.exec(
                            select(TVDBCache).where(TVDBCache.tvdb_id == db_t.tvdb_id)
                        ).first()
                        item["tvdb_series_name_es"] = tvdb_series.series_name_es if tvdb_series else None
                    else:
                        item["tvdb_series_name_es"] = None

                    all_results.append(item)
            except Exception as e:
                logger.error(f"Error Búsqueda {idx.name}: {e}")

        wake_worker(f"búsqueda interactiva '{q}'")
        return {"success": True, "results": all_results}

# ------------------------------------------------------------
# Proxy de imágenes para mostrar pósters de trackers y TheTVDB en
# la UI evitando problemas de cookies, referer o imágenes inválidas.
# ------------------------------------------------------------
@router.get("/poster")
async def proxy_poster(url: str):
    if not url:
        return _poster_fallback_response()

    if _is_placeholder_poster_url(url):
        return _poster_fallback_response()

    is_tvdb = "thetvdb.com" in url.lower()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site"
    }

    if is_tvdb:
        headers["Referer"] = "https://thetvdb.com/"
    else:
        headers["Referer"] = "https://foro.unionfansub.com/"
        with Session(engine) as session:
            idx = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
            if idx and idx.cookie_string:
                headers["Cookie"] = decrypt_secret(idx.cookie_string)

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            final_url = str(resp.url)
            if _is_placeholder_poster_url(final_url):
                return _poster_fallback_response()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return Response(content=resp.content, media_type=content_type)
    except Exception as e:
        logger.error(f"❌ Error proxificando la imagen ({url}): {e}")
        return _poster_fallback_response()

# ------------------------------------------------------------
# Exporta datos de Kitsunarr en JSON para respaldar torrents,
# biblioteca TVDB o el paquete completo.
# ------------------------------------------------------------
@router.get("/cache/export")
async def export_database(module: str = "bundle"):
    with Session(engine) as session:
        if module == "torrents":
            data = export_torrents_only(session)
        elif module == "tvdb":
            data = export_tvdb_only(session)
        else:
            data = export_full_bundle(session)

    payload = json.dumps(data, ensure_ascii=False, indent=4)
    filename = f"kitsunarr_{module}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ------------------------------------------------------------
# Importa un respaldo JSON de Kitsunarr y reconstruye las relaciones
# de caché, TVDB y torrents usando la URL actual del servicio.
# ------------------------------------------------------------
@router.post("/cache/import")
async def import_database(request: Request, file: UploadFile = File(...)):
    try:
        contents = await file.read()
        data = json.loads(contents.decode("utf-8"))
        base_url = str(request.base_url).rstrip("/")
        result = await import_relational_data(data, base_url)
        return {"success": True, "imported": result.get("counts", {})}
    except json.JSONDecodeError:
        return {"success": False, "error": "El archivo no es un JSON válido."}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"❌ Error durante la importación: {e}")
        return {"success": False, "error": str(e)}

# ------------------------------------------------------------
# Devuelve los torrents visibles en qBittorrent para emparejarlos
# manualmente con fichas de Kitsunarr desde el laboratorio.
# ------------------------------------------------------------
@router.get("/qbittorrent/list")
async def get_qbittorrent_list():
    with Session(engine) as session:
        sys_config, sid, qb_error = await _login_qbittorrent_from_config(session)
        if qb_error:
            return {"success": False, "error": qb_error, "torrents": []}

    try:
        torrents = await get_all_unionfansub_torrents(sys_config.qbittorrent_url, sid)
        return {"success": True, "torrents": torrents}
    except Exception as e:
        logger.error(f"❌ Error obteniendo lista de qBittorrent: {e}")
        return {"success": False, "error": str(e), "torrents": []}

# ------------------------------------------------------------
# Datos para vincular manualmente una ficha de Kitsunarr con un
# torrent existente en qBittorrent mediante su info hash.
# ------------------------------------------------------------
class PairTorrentForm(BaseModel):
    guid: str
    info_hash: str

# ------------------------------------------------------------
# Empareja una ficha torrent con qBittorrent y actualiza su
# telemetría inicial para mostrar el estado en Kitsunarr.
# ------------------------------------------------------------
@router.post("/torrent/pair")
async def pair_torrent(data: PairTorrentForm):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == data.guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado en la base de datos."}

        t.info_hash = data.info_hash.lower().strip()
        sys_config, sid, _ = await _login_qbittorrent_from_config(session)
        if sid and sys_config:
            telemetry = await get_torrent_telemetry(sys_config.qbittorrent_url, sid, t.info_hash)
            _apply_telemetry_to_torrent(t, telemetry)
        session.commit()
        logger.info(f"🔗 Torrent {data.guid} emparejado manualmente con hash {data.info_hash}")
        return {"success": True}


# ------------------------------------------------------------
# Refresca y devuelve la telemetría de qBittorrent para una ficha
# local que ya tiene info hash vinculado.
# ------------------------------------------------------------
@router.get("/torrent/{guid}/telemetry")
async def get_torrent_client_telemetry(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado."}
        if not t.info_hash:
            return {"success": False, "error": "La ficha no tiene info_hash vinculado."}

        sys_config, sid, qb_error = await _login_qbittorrent_from_config(session)
        if qb_error:
            return {"success": False, "error": qb_error}

        telemetry = await get_torrent_telemetry(sys_config.qbittorrent_url, sid, t.info_hash)
        _apply_telemetry_to_torrent(t, telemetry)
        session.add(t)
        session.commit()
        session.refresh(t)

        return {
            "success": True,
            "exists_in_client": t.exists_in_client,
            "telemetry": {
                "info_hash": t.info_hash,
                "progress": t.progress,
                "client_status": t.client_status,
                "download_speed": t.download_speed,
                "upload_speed": t.upload_speed,
                "ratio": telemetry.get("ratio") if telemetry else None,
                "eta": t.eta,
                "peers_seeds": t.peers_seeds,
                "peers_leechs": t.peers_leechs,
            }
        }


# ------------------------------------------------------------
# Descarga el .torrent desde el indexador origen, calcula su info
# hash y prepara la ficha para enlazar telemetría de qBittorrent.
# ------------------------------------------------------------
@router.post("/torrent/{guid}/calculate_hash")
async def calculate_torrent_info_hash(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado."}

        indexer = indexer_manager.get_indexer(t.indexer)
        if not indexer:
            return {"success": False, "error": f"El indexador '{t.indexer}' no estÃ¡ disponible."}

        idx_config = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == t.indexer)).first()
        tracker_cookie = decrypt_secret(idx_config.cookie_string) if idx_config and idx_config.cookie_string else ""
        source_guid = t.source_guid or (guid.split("-", 1)[1] if "-" in guid else guid)

        try:
            torrent_bytes = await indexer.download_torrent(source_guid, tracker_cookie)
            torrent_data = bencodepy.decode(torrent_bytes)
            info_dict = torrent_data[b"info"]
            info_hash = hashlib.sha1(bencodepy.encode(info_dict)).hexdigest().lower()
        except Exception as e:
            logger.error(f"âŒ No se pudo calcular Info Hash para {guid}: {e}")
            return {"success": False, "error": "No se pudo descargar o procesar el .torrent origen."}

        t.info_hash = info_hash
        sys_config, sid, _ = await _login_qbittorrent_from_config(session)
        if sid and sys_config:
            telemetry = await get_torrent_telemetry(sys_config.qbittorrent_url, sid, info_hash)
            _apply_telemetry_to_torrent(t, telemetry)

        session.add(t)
        session.commit()
        logger.info(f"âœ… Info Hash calculado manualmente para {guid}: {info_hash}")
        return {"success": True, "info_hash": info_hash}
