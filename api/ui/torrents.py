import hashlib
import asyncio
import httpx
import bencodepy
import re
import uuid
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
REHYDRATE_BATCH_SIZE = 100
_rehydrate_jobs: dict[str, dict] = {}
_rehydrate_jobs_lock = asyncio.Lock()

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
        return config, None, "qBittorrent no esta configurado."
    if not config.qbittorrent_user or not config.qbittorrent_password:
        return config, None, "Faltan usuario o contrasena de qBittorrent."

    password = decrypt_secret(config.qbittorrent_password)
    sid = await qbittorrent_login(config.qbittorrent_url, config.qbittorrent_user, password)
    if sid is None:
        return config, None, "No se pudo iniciar sesion en qBittorrent."
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
            if sid is not None and sys_config:
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
                            peers_seeds=int(r.get("seeders") or 0),
                            peers_leechs=int(r.get("leechers") or 0),
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
                        incoming_seeders = int(r.get("seeders") or 0)
                        incoming_leechers = int(r.get("leechers") or 0)
                        if db_t.peers_seeds != incoming_seeders:
                            db_t.peers_seeds = incoming_seeders
                            updated = True
                        if db_t.peers_leechs != incoming_leechers:
                            db_t.peers_leechs = incoming_leechers
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
# Datos para lanzar una rehidratación de fichas en caché, ya sea de
# un conjunto seleccionado o de todas las fichas locales.
# ------------------------------------------------------------
class RehydrateForm(BaseModel):
    mode: str
    guids: Optional[list[str]] = None

# ------------------------------------------------------------
# Devuelve una lista de lotes consecutivos con tamaño máximo fijo.
# ------------------------------------------------------------
def _chunk_guids(values: list[str], batch_size: int) -> list[list[str]]:
    if batch_size <= 0:
        return [values]
    return [values[i:i + batch_size] for i in range(0, len(values), batch_size)]

# ------------------------------------------------------------
# Actualiza de forma segura el estado runtime de un job de
# rehidratación para que la UI lo consulte por polling.
# ------------------------------------------------------------
async def _update_rehydrate_job(job_id: str, updates: dict):
    async with _rehydrate_jobs_lock:
        job = _rehydrate_jobs.get(job_id)
        if not job:
            return
        job.update(updates)

# ------------------------------------------------------------
# Crea el estado inicial de un job de rehidratación y lo registra en
# memoria para seguimiento de progreso.
# ------------------------------------------------------------
async def _create_rehydrate_job(mode: str, wanted_guids: list[str]) -> str:
    job_id = uuid.uuid4().hex
    now_iso = datetime.utcnow().isoformat()
    async with _rehydrate_jobs_lock:
        _rehydrate_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "mode": mode,
            "wanted_guids": wanted_guids,
            "created_at": now_iso,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "remaining_count": 0,
            "remaining_guids": [],
            "processed": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "batch_size": REHYDRATE_BATCH_SIZE,
            "current_batch": 0,
            "total_batches": 0,
            "current_batch_total": 0,
            "current_batch_processed": 0,
            "batch_percent": 0,
            "percent_total": 0,
            "message": "En cola",
            "error": None,
            "cancel_requested": False,
        }
    return job_id

# ------------------------------------------------------------
# Normaliza el source_guid real de una ficha de caché para que el
# indexador pueda ir directo a la URL de detalles del tracker.
# ------------------------------------------------------------
def _resolve_source_guid_for_rehydrate(torrent: TorrentCache) -> str:
    if torrent.source_guid:
        return str(torrent.source_guid).strip()
    guid = str(torrent.guid or "").strip()
    if "-" in guid:
        return guid.split("-", 1)[1].strip()
    return guid

# ------------------------------------------------------------
# Ejecuta en segundo plano la rehidratación de fichas y actualiza el
# estado del job para que la UI muestre progreso por lotes y total.
# ------------------------------------------------------------
async def _run_rehydrate_job(job_id: str):
    async with _rehydrate_jobs_lock:
        job = _rehydrate_jobs.get(job_id)
        if not job:
            return
        mode = job.get("mode", "selected")
        wanted_guids = list(job.get("wanted_guids") or [])
        job["status"] = "running"
        job["started_at"] = datetime.utcnow().isoformat()
        job["message"] = "Preparando lotes"
        job["cancel_requested"] = False

    try:
        async with _rehydrate_jobs_lock:
            existing_queue = list(_rehydrate_jobs.get(job_id, {}).get("remaining_guids") or [])

        if existing_queue:
            guid_queue = [str(g).strip() for g in existing_queue if str(g).strip()]
        else:
            with Session(engine) as session:
                if mode == "selected":
                    torrents = session.exec(
                        select(TorrentCache).where(TorrentCache.guid.in_(wanted_guids))
                    ).all() if wanted_guids else []
                else:
                    torrents = session.exec(
                        select(TorrentCache).order_by(desc(TorrentCache.pub_date))
                    ).all()
            guid_queue = [str(t.guid).strip() for t in torrents if str(t.guid).strip()]

        total = len(guid_queue)
        if total == 0:
            await _update_rehydrate_job(job_id, {
                "status": "failed",
                "finished_at": datetime.utcnow().isoformat(),
                "error": "No hay fichas para rehidratar con ese criterio.",
                "message": "Sin fichas para procesar",
            })
            return

        batches = _chunk_guids(guid_queue, REHYDRATE_BATCH_SIZE)
        await _update_rehydrate_job(job_id, {
            "total": total,
            "remaining_count": total,
            "remaining_guids": list(guid_queue),
            "total_batches": len(batches),
            "message": "Procesando fichas",
        })

        processed = 0
        updated = 0
        unchanged = 0
        skipped = 0
        failed = 0

        for batch_idx, batch_guids in enumerate(batches):
            async with _rehydrate_jobs_lock:
                job_snapshot = _rehydrate_jobs.get(job_id) or {}
                if job_snapshot.get("cancel_requested"):
                    await _update_rehydrate_job(job_id, {
                        "status": "cancelled",
                        "finished_at": datetime.utcnow().isoformat(),
                        "message": "Rehidratación cancelada por el usuario",
                    })
                    logger.warning(
                        f"⏹️ [CACHE] [REHYDRATE] Cancelado por usuario antes de lote {batch_idx + 1}/{len(batches)} | "
                        f"procesadas={processed} | restantes={len(guid_queue) - processed}"
                    )
                    return

            await _update_rehydrate_job(job_id, {
                "current_batch": batch_idx + 1,
                "current_batch_total": len(batch_guids),
                "current_batch_processed": 0,
                "batch_percent": 0,
                "message": f"Procesando lote {batch_idx + 1}/{len(batches)}",
            })

            with Session(engine) as session:
                batch_torrents = session.exec(
                    select(TorrentCache).where(TorrentCache.guid.in_(batch_guids))
                ).all()
                torrents_by_guid = {t.guid: t for t in batch_torrents}

                indexer_ids = sorted({
                    (t.indexer or "").strip()
                    for t in batch_torrents
                    if (t.indexer or "").strip()
                })
                indexer_configs = {}
                for idx_id in indexer_ids:
                    idx_conf = session.exec(
                        select(IndexerConfig).where(IndexerConfig.identifier == idx_id)
                    ).first()
                    if idx_conf:
                        indexer_configs[idx_id] = idx_conf

                for pos, guid in enumerate(batch_guids):
                    async with _rehydrate_jobs_lock:
                        job_snapshot = _rehydrate_jobs.get(job_id) or {}
                        if job_snapshot.get("cancel_requested"):
                            remaining_tail = [x for x in guid_queue[processed:] if str(x).strip()]
                            await _update_rehydrate_job(job_id, {
                                "status": "cancelled",
                                "finished_at": datetime.utcnow().isoformat(),
                                "remaining_guids": remaining_tail,
                                "remaining_count": len(remaining_tail),
                                "message": "Rehidratación cancelada por el usuario",
                            })
                            logger.warning(
                                f"⏹️ [CACHE] [REHYDRATE] Cancelado por usuario en lote {batch_idx + 1}/{len(batches)} | "
                                f"procesadas={processed} | restantes={len(remaining_tail)}"
                            )
                            return

                    t = torrents_by_guid.get(guid)
                    if not t:
                        processed += 1
                        skipped += 1
                        remaining_now = max(0, total - processed)
                        await _update_rehydrate_job(job_id, {
                            "processed": processed,
                            "skipped": skipped,
                            "remaining_count": remaining_now,
                            "remaining_guids": [x for x in guid_queue[processed:] if str(x).strip()],
                            "current_batch_processed": pos + 1,
                            "batch_percent": int(((pos + 1) / max(1, len(batch_guids))) * 100),
                            "percent_total": int((processed / max(1, total)) * 100),
                        })
                        continue

                    idx_id = (t.indexer or "").strip()
                    display_title = (t.original_title or t.enriched_title or f"Torrent {t.guid}").strip()

                    if not idx_id:
                        skipped += 1
                        logger.warning(f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={t.guid} | Título='{display_title}' | Motivo='Sin indexador'.")
                    else:
                        indexer = indexer_manager.get_indexer(idx_id)
                        idx_conf = indexer_configs.get(idx_id)

                        if not indexer:
                            skipped += 1
                            logger.warning(f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={t.guid} | Título='{display_title}' | Motivo='Indexador no implementado: {idx_id}'.")
                        elif not idx_conf:
                            skipped += 1
                            logger.warning(f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={t.guid} | Título='{display_title}' | Motivo='Indexador no configurado: {idx_id}'.")
                        elif not hasattr(indexer, "rehydrate_torrent"):
                            skipped += 1
                            logger.warning(f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={t.guid} | Título='{display_title}' | Motivo='Indexador sin soporte de rehidratación: {idx_id}'.")
                        else:
                            source_guid = _resolve_source_guid_for_rehydrate(t)
                            if not source_guid:
                                skipped += 1
                                logger.warning(f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={t.guid} | Título='{display_title}' | Motivo='No se pudo resolver source_guid'.")
                            else:
                                tracker_cookie = decrypt_secret(idx_conf.cookie_string) if idx_conf.cookie_string else ""
                                base_title = (t.enriched_title or t.original_title or "").strip()
                                base_title = re.sub(r"\s*\[[^\]]+\]\s*$", "", base_title).strip()

                                payload = await indexer.rehydrate_torrent(
                                    source_guid=source_guid,
                                    cookie_string=tracker_cookie,
                                    base_title=base_title,
                                    current_original_title=t.original_title or "",
                                )
                                
                                await asyncio.sleep(0.5)
                                
                                if not payload:
                                    failed += 1
                                    logger.error(f"❌ [CACHE] [REHYDRATE] MISS | ID={t.guid} | Título='{display_title}' | source_guid={source_guid}")
                                else:
                                    changed = False
                                    if payload.get("title") and t.enriched_title != payload.get("title"):
                                        t.enriched_title = payload.get("title")
                                        changed = True
                                    if payload.get("original_title") and t.original_title != payload.get("original_title"):
                                        t.original_title = payload.get("original_title")
                                        changed = True
                                    if payload.get("description") and t.description != payload.get("description"):
                                        t.description = payload.get("description")
                                        changed = True
                                    if payload.get("poster_url") and t.poster_url != payload.get("poster_url"):
                                        t.poster_url = payload.get("poster_url")
                                        changed = True
                                    if payload.get("size_bytes") is not None and payload.get("size_bytes", 0) > 0 and t.size_bytes != payload.get("size_bytes"):
                                        t.size_bytes = payload.get("size_bytes")
                                        changed = True
                                    incoming_seeders = int(payload.get("seeders") or 0)
                                    incoming_leechers = int(payload.get("leechers") or 0)
                                    if t.peers_seeds != incoming_seeders:
                                        t.peers_seeds = incoming_seeders
                                        changed = True
                                    if t.peers_leechs != incoming_leechers:
                                        t.peers_leechs = incoming_leechers
                                        changed = True
                                    if payload.get("publish_date") and t.pub_date != payload.get("publish_date"):
                                        t.pub_date = payload.get("publish_date")
                                        changed = True
                                    if payload.get("freeleech_until") is not None and t.freeleech_until != payload.get("freeleech_until"):
                                        t.freeleech_until = payload.get("freeleech_until")
                                        changed = True
                                    if payload.get("raw_filenames") and t.raw_filenames != payload.get("raw_filenames"):
                                        t.raw_filenames = payload.get("raw_filenames")
                                        changed = True
                                    if payload.get("tags") and t.tags != payload.get("tags"):
                                        t.tags = payload.get("tags")
                                        changed = True

                                    source_label = payload.get("source_guid") or source_guid
                                    if changed:
                                        session.add(t)
                                        session.commit()
                                        updated += 1
                                        logger.info(f"♻️ [CACHE] [REHYDRATE] HIT | ID={t.guid} | Título='{(t.original_title or display_title).strip()}' | source_guid={source_label}")
                                    else:
                                        unchanged += 1
                                        logger.info(f"ℹ️ [CACHE] [REHYDRATE] HIT-SIN-CAMBIOS | ID={t.guid} | Título='{display_title}' | source_guid={source_label}")

                    processed += 1
                    remaining_now = max(0, total - processed)
                    remaining_queue = [x for x in guid_queue[processed:] if str(x).strip()]
                    await _update_rehydrate_job(job_id, {
                        "processed": processed,
                        "updated": updated,
                        "unchanged": unchanged,
                        "skipped": skipped,
                        "failed": failed,
                        "remaining_count": remaining_now,
                        "remaining_guids": remaining_queue,
                        "current_batch_processed": pos + 1,
                        "batch_percent": int(((pos + 1) / max(1, len(batch_guids))) * 100),
                        "percent_total": int((processed / max(1, total)) * 100),
                    })


        await _update_rehydrate_job(job_id, {
            "status": "completed",
            "finished_at": datetime.utcnow().isoformat(),
            "message": "Rehidratación completada",
            "percent_total": 100,
            "batch_percent": 100,
            "remaining_count": 0,
            "remaining_guids": [],
        })
        logger.info(
            f"✅ [CACHE] [REHYDRATE] Proceso completado | modo={mode} | "
            f"procesadas={processed} | actualizadas={updated} | sin_cambios={unchanged} | "
            f"omitidas={skipped} | fallidas={failed}"
        )
    except Exception as e:
        await _update_rehydrate_job(job_id, {
            "status": "failed",
            "finished_at": datetime.utcnow().isoformat(),
            "error": str(e),
            "message": "Error durante la rehidratación",
        })
        logger.error(f"❌ [CACHE] [REHYDRATE] Error crítico en job {job_id}: {e}")

# ------------------------------------------------------------
# Arranca un job de rehidratación en segundo plano y devuelve su ID
# para que la UI siga el progreso mediante polling.
# ------------------------------------------------------------
@router.post("/cache/rehydrate")
async def start_rehydrate_cache_entries(data: RehydrateForm):
    mode = (data.mode or "").strip().lower()
    if mode not in ["selected", "all"]:
        return {"success": False, "error": "Modo inválido. Usa 'selected' o 'all'."}
    if mode == "selected" and not data.guids:
        return {"success": False, "error": "No se han recibido GUIDs para rehidratar."}

    wanted_guids = [str(g).strip() for g in (data.guids or []) if str(g).strip()] if mode == "selected" else []
    job_id = await _create_rehydrate_job(mode, wanted_guids)
    asyncio.create_task(_run_rehydrate_job(job_id))
    return {"success": True, "job_id": job_id}

# ------------------------------------------------------------
# Devuelve el estado actual de un job de rehidratación para mostrar
# progreso dinámico en la interfaz de caché.
# ------------------------------------------------------------
@router.get("/cache/rehydrate/{job_id}/status")
async def get_rehydrate_job_status(job_id: str):
    async with _rehydrate_jobs_lock:
        job = _rehydrate_jobs.get(job_id)
        if not job:
            return {"success": False, "error": "Job de rehidratación no encontrado."}
        snapshot = dict(job)
        snapshot.pop("wanted_guids", None)
        snapshot.pop("remaining_guids", None)
    return {"success": True, "job": snapshot}

# ------------------------------------------------------------
# Marca un job de rehidratación en curso para cancelación segura al
# finalizar la ficha actual.
# ------------------------------------------------------------
@router.post("/cache/rehydrate/{job_id}/cancel")
async def cancel_rehydrate_job(job_id: str):
    async with _rehydrate_jobs_lock:
        job = _rehydrate_jobs.get(job_id)
        if not job:
            return {"success": False, "error": "Job de rehidratación no encontrado."}
        if job.get("status") != "running":
            return {"success": False, "error": "Solo se puede cancelar un job en ejecución."}
        job["cancel_requested"] = True
        job["message"] = "Cancelación solicitada por el usuario"
    logger.warning(f"⏸️ [CACHE] [REHYDRATE] Solicitud de cancelación recibida | job_id={job_id}")
    return {"success": True}

# ------------------------------------------------------------
# Reanuda un job cancelado reutilizando la cola pendiente guardada en
# memoria y manteniendo métricas acumuladas.
# ------------------------------------------------------------
@router.post("/cache/rehydrate/{job_id}/resume")
async def resume_rehydrate_job(job_id: str):
    async with _rehydrate_jobs_lock:
        job = _rehydrate_jobs.get(job_id)
        if not job:
            return {"success": False, "error": "Job de rehidratación no encontrado."}
        if job.get("status") != "cancelled":
            return {"success": False, "error": "Solo se puede reanudar un job cancelado."}
        remaining = list(job.get("remaining_guids") or [])
        if not remaining:
            return {"success": False, "error": "No hay fichas pendientes para reanudar."}
        job["status"] = "queued"
        job["finished_at"] = None
        job["message"] = "Reanudación en cola"
        job["cancel_requested"] = False
        job["current_batch_processed"] = 0
        job["batch_percent"] = 0
    logger.info(f"▶️ [CACHE] [REHYDRATE] Reanudación solicitada | job_id={job_id} | pendientes={len(remaining)}")
    asyncio.create_task(_run_rehydrate_job(job_id))
    return {"success": True}

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
        if sid is not None and sys_config:
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
            return {"success": False, "error": f"El indexador '{t.indexer}' no esta disponible."}

        idx_config = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == t.indexer)).first()
        tracker_cookie = decrypt_secret(idx_config.cookie_string) if idx_config and idx_config.cookie_string else ""
        source_guid = t.source_guid or (guid.split("-", 1)[1] if "-" in guid else guid)

        try:
            torrent_bytes = await indexer.download_torrent(source_guid, tracker_cookie)
            torrent_data = bencodepy.decode(torrent_bytes)
            info_dict = torrent_data[b"info"]
            info_hash = hashlib.sha1(bencodepy.encode(info_dict)).hexdigest().lower()
        except Exception as e:
            logger.error(f"No se pudo calcular Info Hash para {guid}: {e}")
            return {"success": False, "error": "No se pudo descargar o procesar el .torrent origen."}

        t.info_hash = info_hash
        sys_config, sid, _ = await _login_qbittorrent_from_config(session)
        if sid is not None and sys_config:
            telemetry = await get_torrent_telemetry(sys_config.qbittorrent_url, sid, info_hash)
            _apply_telemetry_to_torrent(t, telemetry)

        session.add(t)
        session.commit()
        logger.info(f"Info Hash calculado manualmente para {guid}: {info_hash}")
        return {"success": True, "info_hash": info_hash}
