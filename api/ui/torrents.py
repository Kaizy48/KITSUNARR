import asyncio
import httpx
import re
import uuid
from fastapi import APIRouter, Request, Response, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, StrictInt
from typing import Optional
from sqlmodel import Session, select, desc, or_
import json
from datetime import datetime, timedelta, timezone

from core.database.engine import engine
from core.database.models import SystemConfig, TorrentCache, TVDBCache, IndexerConfig
from core.app.logger import logger
from core.app.background import wake_worker
from core.app.encrypt import decrypt_secret
from core.app.indexers.manager import indexer_manager
from core.app.indexers.snapshots import merge_tracker_metadata_json
from services.torrent.qbittorrent import (
    QBittorrentError,
    add_torrent,
    get_all_unionfansub_torrents,
    get_torrent_telemetry,
)
from services.torrent.metainfo import calculate_metainfo_hash
from services.tvdb.tvdb_api import fetch_full_tvdb_series
from core.app.export import export_torrents_only, export_tvdb_only, export_full_bundle, import_relational_data

router = APIRouter(prefix="/api/ui", tags=["UI Torrents"])
REHYDRATE_SCRAPE_REVISION = max(
    1,
    int(getattr(indexer_manager.get_indexer("unionfansub"), "SCRAPE_REVISION", 1)),
)
REHYDRATE_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
REHYDRATE_TRACKER_FIELDS = (
    "source_guid",
    "tracker_title",
    "fansub_name",
    "original_title",
    "enriched_title",
    "description",
    "poster_url",
    "forum_url",
    "screenshots_json",
    "tracker_metadata_json",
    "file_entries_json",
    "size_bytes",
    "published_at",
    "tracker_freeleech",
    "freeleech_until",
    "raw_filenames",
    "tags",
    "tracker_seeders",
    "tracker_leechers",
)
_rehydrate_jobs_lock = asyncio.Lock()
_rehydrate_active_job: Optional[dict] = None
_rehydrate_last_terminal_job: Optional[dict] = None
_rehydrate_active_task: Optional[asyncio.Task] = None

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
# Devuelve el instante UTC actual como datetime naive, que es la convención de
# las columnas SQLite de este módulo. No recibe entradas ni produce efectos,
# no lanza errores esperados y usa datetime aware para evitar utcnow deprecado.
# ------------------------------------------------------------
def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------
# Serializa un modelo SQLModel a dict sin warnings de Pydantic 2 y conserva
# compatibilidad con runtimes antiguos. Recibe un modelo, devuelve una copia,
# no muta estado ni hace I/O y propaga errores del serializador disponible.
# ------------------------------------------------------------
def _model_dump(model) -> dict:
    modern_dump = getattr(model, "model_dump", None)
    if callable(modern_dump):
        return modern_dump()
    return model.dict()


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
    reset_description_override: bool = False
    tvdb_id: Optional[str] = None
    parsed_season: Optional[int] = None
    is_batch: bool = False
    tags: Optional[str] = None
    reset_tags_override: bool = False
    rename_mapping: Optional[str] = None

# ------------------------------------------------------------
# Prepara una fila TorrentCache para la WebUI y devuelve un dict serializable con
# overrides manuales y freeleech efectivo. No modifica la fila ni hace I/O; deja
# propagar errores de serialización de _model_dump. Depende de _model_dump,
# _as_utc_naive_datetime, _utcnow_naive y del ownership de campos TorrentCache,
# y mantiene compatibilidad con filas que solo tengan fecha freeleech legacy.
# ------------------------------------------------------------
def _enrich_torrent_for_ui(t: TorrentCache) -> dict:
    item = _model_dump(t)
    item["tracker_description"] = t.description
    item["tracker_tags"] = t.tags
    item["has_description_override"] = t.description_override is not None
    item["has_tags_override"] = t.tags_override is not None
    item["description"] = t.description_override if t.description_override is not None else t.description
    item["tags"] = t.tags_override if t.tags_override is not None else t.tags
    freeleech_until = _as_utc_naive_datetime(t.freeleech_until)
    now_utc = _utcnow_naive()
    if t.tracker_freeleech is None:
        item["is_freeleech"] = bool(freeleech_until and freeleech_until > now_utc)
    else:
        item["is_freeleech"] = bool(t.tracker_freeleech) and bool(
            freeleech_until is None or freeleech_until > now_utc
        )
    return item


# ------------------------------------------------------------
# Normaliza tamaños remotos antes de persistirlos. Recibe valores legacy o
# tipados, devuelve bytes enteros solo si son mayores que uno y descarta
# None/booleanos/0/1/formato inválido; no hace I/O ni altera la entrada.
# ------------------------------------------------------------
def _normalize_tracker_size_bytes(value) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized > 1 else None


# ------------------------------------------------------------
# Convierte una entrada datetime o ISO del scraper al datetime UTC-naive usado
# por SQLite. Devuelve None ante ausencia/tipo/formato inválido, no hace I/O ni
# muta la entrada y no propaga esos errores de parseo; depende de datetime y
# timezone, no interpreta fechas RFC legacy y evita comparar aware con naive.
# ------------------------------------------------------------
def _as_utc_naive_datetime(value) -> Optional[datetime]:
    parsed = value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    if not isinstance(parsed, datetime):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# ------------------------------------------------------------
# Lee la conexión qBittorrent desde una Session ya abierta y devuelve config,
# password descifrada y error lógico. No hace red ni confirma/muta la DB; permite
# cerrar SQLite antes del await WebAPI. Depende de decrypt_secret y SystemConfig;
# datos incompletos se comunican como error sin exponer secretos.
# ------------------------------------------------------------
def _load_qbittorrent_access(
    session: Session,
) -> tuple[Optional[SystemConfig], Optional[str], Optional[str]]:
    config = session.exec(select(SystemConfig)).first()
    if not config or not config.qbittorrent_url:
        return config, None, "qBittorrent no esta configurado."
    if not config.qbittorrent_user or not config.qbittorrent_password:
        return config, None, "Faltan usuario o contrasena de qBittorrent."

    password = decrypt_secret(config.qbittorrent_password)
    return config, password, None


# ------------------------------------------------------------
# Copia una respuesta opcional de qBittorrent sobre los campos locales de
# telemetría de una ficha y no toca tracker_seeders/tracker_leechers.
# Recibe la fila mutable y el DTO del cliente; no confirma la Session y
# deja al caller decidir la transacción. Un valor None marca no encontrado.
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
# Devuelve, sin entradas, un dict JSON con torrents sin vincular, series TVDB y
# lista plana limitada y ordenada por published_at. Lee SQLite en una Session de
# solo lectura, sin red ni escrituras; los errores DB/serialización se propagan al
# handler global. Depende de Session/select, _model_dump y _enrich_torrent_for_ui.
# ------------------------------------------------------------
@router.get("/cache")
async def get_torrent_cache():
    with Session(engine) as session:
        unlinked_torrents_db = session.exec(
            select(TorrentCache).where(
                (TorrentCache.tvdb_id == None) | (TorrentCache.tvdb_status != "Listo")
            ).order_by(desc(TorrentCache.published_at)).limit(1000)
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
                s_item = _model_dump(s)
                s_item["linked_torrents_count"] = linked_counts.get(s.tvdb_id, 0)
                linked_series.append(s_item)

        all_torrents_db = session.exec(
            select(TorrentCache).order_by(desc(TorrentCache.published_at)).limit(2000)
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
# Devuelve la vista de una serie TVDB y sus torrents locales validados.
# Recibe el tvdb_id de ruta, consulta TVDBCache/TorrentCache en una Session,
# ordena por published_at y serializa overrides; responde error lógico si la
# serie no existe y no produce escrituras ni llamadas remotas.
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
            ).order_by(desc(TorrentCache.published_at))
        ).all()

        return {
            "success": True,
            "series": _model_dump(series),
            "torrents": [_enrich_torrent_for_ui(t) for t in torrents]
        }

# ------------------------------------------------------------
# Devuelve la ficha técnica por GUID y refresca qBittorrent cuando hay hash.
# Separa lectura/config, await remoto y escritura corta; una respuesta válida vacía
# marca not_found, mientras fallo qB conserva la última telemetría. Devuelve el
# envelope de detalle, no eleva errores qB al cliente y depende de
# _load_qbittorrent_access/get_torrent_telemetry/_apply_telemetry_to_torrent.
# ------------------------------------------------------------
@router.get("/cache/torrent/{guid}")
async def get_torrent_detail(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado"}
        info_hash = t.info_hash
        qb_config, qb_password, qb_error = (
            _load_qbittorrent_access(session) if info_hash else (None, None, None)
        )

    telemetry = None
    telemetry_observed = False
    if info_hash and qb_config and qb_password is not None and not qb_error:
        try:
            telemetry = await get_torrent_telemetry(
                qb_config.qbittorrent_url,
                qb_config.qbittorrent_user,
                qb_password,
                info_hash,
            )
            telemetry_observed = True
        except QBittorrentError as exc:
            logger.warning(f"⚠️ qBittorrent no actualizó la ficha {guid}: {exc}")

    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado"}
        if telemetry_observed:
            _apply_telemetry_to_torrent(t, telemetry)
            session.add(t)
            session.commit()
            session.refresh(t)

        item = _enrich_torrent_for_ui(t)
        item["ratio"] = telemetry.get("ratio") if telemetry else None
        if t.tvdb_id:
            series = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == t.tvdb_id)).first()
            if series:
                item["tvdb_series_name_es"] = series.series_name_es

        return {"success": True, "torrent": item}

# ------------------------------------------------------------
# Actualiza los campos manuales de una ficha y devuelve un dict success/error.
# Recibe GUID y CacheEditForm; los reset restauran ownership al tracker y un valor
# efectivo idéntico no crea override. Escribe/commit en SQLite y puede crear una
# task de fetch_full_tvdb_series mediante asyncio.create_task; una ficha ausente
# es error lógico y los errores DB/task se propagan al handler global.
# ------------------------------------------------------------
@router.put("/cache/{guid}")
async def update_cache_entry(guid: str, data: CacheEditForm):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False}

        linked_tvdb_id = None
        provided_fields = getattr(data, "model_fields_set", None)
        if provided_fields is None:
            provided_fields = getattr(data, "__fields_set__", set())

        t.ai_translated_title = data.ai_translated_title
        effective_description = t.description_override if t.description_override is not None else t.description
        if data.reset_description_override:
            t.description_override = None
        elif "description" in provided_fields and data.description != effective_description:
            t.description_override = data.description
        t.parsed_season = data.parsed_season
        t.is_batch = data.is_batch

        effective_tags = t.tags_override if t.tags_override is not None else t.tags
        if data.reset_tags_override:
            t.tags_override = None
        elif "tags" in provided_fields and data.tags != effective_tags:
            t.tags_override = data.tags
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
# Persiste en una transacción breve los resultados ya obtenidos de un indexador
# y devuelve los DTO enriquecidos de la WebUI. Recibe payloads normalizados,
# configuración separada de la Session y URL pública; puede escribir/actualizar
# TorrentCache, propaga fallos SQLite al endpoint y depende de los helpers de
# fechas, tamaños, metadata y ownership. No realiza ninguna petición de red.
# ------------------------------------------------------------
def _persist_interactive_search_results(
    results: list[dict],
    indexer_identifier: str,
    indexer_name: str,
    base_url: str,
) -> list[dict]:
    result_guids = [str(item.get("guid")) for item in results if item.get("guid")]
    rendered_results: list[dict] = []

    with Session(engine) as session:
        existing_by_guid: dict[str, TorrentCache] = {}
        if result_guids:
            existing_rows = session.exec(
                select(TorrentCache).where(TorrentCache.guid.in_(result_guids))
            ).all()
            existing_by_guid = {row.guid: row for row in existing_rows}

        for result in results:
            guid = str(result.get("guid") or "").strip()
            if not guid:
                continue

            db_torrent = existing_by_guid.get(guid)
            fallback_title = str(
                result.get("original_title") or result.get("title") or f"Torrent {guid}"
            ).strip()
            logger.info(
                f"📦 [CACHE] [UI] {'HIT' if db_torrent else 'MISS'} | "
                f"ID={guid} | Título='{fallback_title}'"
            )

            if db_torrent is None:
                incoming_metadata = result.get("tracker_metadata")
                tracker_metadata_json = (
                    merge_tracker_metadata_json(None, incoming_metadata)
                    if isinstance(incoming_metadata, dict)
                    else result.get("tracker_metadata_json")
                )
                freeleech_is_observed = (
                    "freeleech_present" in result
                    and result.get("freeleech_present") is not None
                )
                incoming_freeleech = (
                    bool(result.get("freeleech_present"))
                    if freeleech_is_observed
                    else None
                )
                db_torrent = TorrentCache(
                    guid=guid,
                    source_guid=(
                        str(result.get("source_guid")).strip()
                        if result.get("source_guid") is not None
                        else None
                    ),
                    tracker_title=result.get("tracker_title"),
                    original_title=result.get("original_title") or fallback_title,
                    enriched_title=result.get("title") or fallback_title,
                    description=result.get("description"),
                    poster_url=result.get("poster_url"),
                    forum_url=result.get("forum_url"),
                    screenshots_json=result.get("screenshots_json"),
                    tracker_metadata_json=tracker_metadata_json,
                    file_entries_json=result.get("file_entries_json"),
                    fansub_name=(
                        result.get("fansub_name") or result.get("fansub") or indexer_name
                    ),
                    indexer=indexer_identifier,
                    download_url=f"{base_url}/api/download/{guid}_base",
                    published_at=_as_utc_naive_datetime(result.get("published_at")),
                    last_scraped_at=_as_utc_naive_datetime(result.get("last_scraped_at")),
                    scrape_revision=int(result.get("scrape_revision") or 0),
                    tracker_freeleech=incoming_freeleech,
                    freeleech_until=(
                        _as_utc_naive_datetime(result.get("freeleech_until"))
                        if incoming_freeleech is not False
                        else None
                    ),
                    size_bytes=_normalize_tracker_size_bytes(result.get("size_bytes")),
                    tracker_seeders=result.get("tracker_seeders", result.get("seeders")),
                    tracker_leechers=result.get("tracker_leechers", result.get("leechers")),
                    raw_filenames=result.get("raw_filenames"),
                    tags=result.get("tags"),
                )
                session.add(db_torrent)
                existing_by_guid[guid] = db_torrent
            else:
                updated = False
                incoming_source_guid = str(result.get("source_guid") or "").strip()
                if incoming_source_guid and db_torrent.source_guid != incoming_source_guid:
                    db_torrent.source_guid = incoming_source_guid
                    updated = True

                for field_name in (
                    "tracker_title",
                    "description",
                    "poster_url",
                    "forum_url",
                    "screenshots_json",
                    "file_entries_json",
                    "tracker_seeders",
                    "tracker_leechers",
                ):
                    incoming_value = result.get(field_name)
                    if incoming_value is not None and getattr(db_torrent, field_name) != incoming_value:
                        setattr(db_torrent, field_name, incoming_value)
                        updated = True

                incoming_metadata = result.get("tracker_metadata")
                if isinstance(incoming_metadata, dict):
                    merged_metadata = merge_tracker_metadata_json(
                        db_torrent.tracker_metadata_json,
                        incoming_metadata,
                    )
                    if db_torrent.tracker_metadata_json != merged_metadata:
                        db_torrent.tracker_metadata_json = merged_metadata
                        updated = True

                for field_name in ("published_at", "last_scraped_at"):
                    incoming_datetime = _as_utc_naive_datetime(result.get(field_name))
                    if (
                        incoming_datetime is not None
                        and getattr(db_torrent, field_name) != incoming_datetime
                    ):
                        setattr(db_torrent, field_name, incoming_datetime)
                        updated = True

                if "freeleech_present" in result and result.get("freeleech_present") is not None:
                    incoming_freeleech = bool(result.get("freeleech_present"))
                    incoming_expiry = (
                        _as_utc_naive_datetime(result.get("freeleech_until"))
                        if incoming_freeleech
                        else None
                    )
                    if db_torrent.tracker_freeleech != incoming_freeleech:
                        db_torrent.tracker_freeleech = incoming_freeleech
                        updated = True
                    if db_torrent.freeleech_until != incoming_expiry:
                        db_torrent.freeleech_until = incoming_expiry
                        updated = True

                incoming_size = _normalize_tracker_size_bytes(result.get("size_bytes"))
                if incoming_size is not None and db_torrent.size_bytes != incoming_size:
                    db_torrent.size_bytes = incoming_size
                    updated = True
                if result.get("title") and db_torrent.enriched_title != result.get("title"):
                    db_torrent.enriched_title = result.get("title")
                    updated = True
                if (
                    result.get("original_title")
                    and db_torrent.original_title != result.get("original_title")
                ):
                    db_torrent.original_title = result.get("original_title")
                    updated = True
                if result.get("raw_filenames") and not db_torrent.raw_filenames:
                    db_torrent.raw_filenames = result.get("raw_filenames")
                    updated = True
                if result.get("tags") and not db_torrent.tags:
                    db_torrent.tags = result.get("tags")
                    updated = True
                try:
                    incoming_revision = max(0, int(result.get("scrape_revision")))
                except (TypeError, ValueError):
                    incoming_revision = None
                if (
                    incoming_revision is not None
                    and incoming_revision > int(db_torrent.scrape_revision or 0)
                ):
                    db_torrent.scrape_revision = incoming_revision
                    updated = True
                incoming_fansub = (
                    result.get("fansub_name") or result.get("fansub") or indexer_name
                )
                if incoming_fansub and db_torrent.fansub_name != incoming_fansub:
                    db_torrent.fansub_name = incoming_fansub
                    updated = True
                if updated:
                    session.add(db_torrent)

            item = _enrich_torrent_for_ui(db_torrent)
            if db_torrent.tvdb_id:
                tvdb_series = session.exec(
                    select(TVDBCache).where(TVDBCache.tvdb_id == db_torrent.tvdb_id)
                ).first()
                item["tvdb_series_name_es"] = tvdb_series.series_name_es if tvdb_series else None
            else:
                item["tvdb_series_name_es"] = None
            rendered_results.append(item)

        session.commit()

    return rendered_results


# ------------------------------------------------------------
# Ejecuta una búsqueda interactiva en los indexadores activos y devuelve los
# DTOs persistidos para la WebUI. Recibe consulta + Request, hace I/O remoto sin
# retener conexiones SQLite y después usa transacciones cortas por indexador;
# aísla errores de red/parser/DB por proveedor en logs, despierta el worker y
# depende del manager, credenciales cifradas y _persist_interactive_search_results.
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
        has_configured_indexers = bool(session.exec(select(IndexerConfig)).first())

    if not active_indexers:
        if has_configured_indexers:
            return {
                "success": False,
                "error": (
                    "Hay indexadores configurados, pero están deshabilitados. "
                    "Actívalos en la vista de Indexadores."
                ),
            }
        return {"success": False, "error": "No hay indexadores configurados todavía."}

    base_url = str(request.base_url).rstrip("/")
    all_results: list[dict] = []
    for indexer_config in active_indexers:
        indexer = indexer_manager.get_indexer(indexer_config.identifier)
        if not indexer:
            continue

        cookie = (
            decrypt_secret(indexer_config.cookie_string)
            if indexer_config.cookie_string
            else ""
        )
        try:
            remote_results = await indexer.search(q, cookie)
            all_results.extend(_persist_interactive_search_results(
                remote_results,
                indexer_config.identifier,
                indexer_config.name,
                base_url,
            ))
        except Exception as error:
            logger.error(f"Error Búsqueda {indexer_config.name}: {error}")

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
# Exporta el module solicitado (torrents, tvdb o bundle) y devuelve una Response
# JSON descargable. Lee SQLite y serializa en memoria sin modificar datos ni hacer
# red; errores DB/JSON se propagan. Depende de los tres exporters relacionales,
# Session, json.dumps y _utcnow_naive para contenido, nombre y fecha del fichero.
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
    filename = f"kitsunarr_{module}_{_utcnow_naive().strftime('%Y%m%d_%H%M%S')}.json"
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
# Devuelve los torrents visibles para emparejamiento manual. Lee y descifra config
# en una Session corta, la cierra y consulta la WebAPI con sesión reutilizable.
# Retorna success=false ante configuración/auth/red/protocolo, sin disfrazar el
# fallo como lista vacía. Depende de _load_qbittorrent_access y del cliente qB.
# ------------------------------------------------------------
@router.get("/qbittorrent/list")
async def get_qbittorrent_list():
    with Session(engine) as session:
        sys_config, password, qb_error = _load_qbittorrent_access(session)
        if qb_error:
            return {"success": False, "error": qb_error, "torrents": []}

    try:
        torrents = await get_all_unionfansub_torrents(
            sys_config.qbittorrent_url,
            sys_config.qbittorrent_user,
            password,
        )
        return {"success": True, "torrents": torrents}
    except QBittorrentError as exc:
        logger.error(f"❌ Error obteniendo lista de qBittorrent: {exc}")
        return {"success": False, "error": str(exc), "torrents": []}

# ------------------------------------------------------------
# Datos para vincular manualmente una ficha de Kitsunarr con un
# torrent existente en qBittorrent mediante su info hash.
# ------------------------------------------------------------
class PairTorrentForm(BaseModel):
    guid: str
    info_hash: str

# ------------------------------------------------------------
# Valida la petición de rehydrate separando ámbito y política de frescura.
# La ausencia de refresh_policy conserva el comportamiento completo del
# cliente antiguo; StrictInt impide aceptar días fraccionarios o booleanos.
# ------------------------------------------------------------
class RehydrateForm(BaseModel):
    mode: str
    guids: Optional[list[str]] = None
    refresh_policy: Optional[str] = None
    older_than_days: Optional[StrictInt] = None
    request_id: Optional[str] = None

# ------------------------------------------------------------
# Recibe un mapping de job ya protegido por _rehydrate_jobs_lock y devuelve una
# copia dict pública/escalar para polling. No muta estado ni hace I/O y conserva
# cancel_requested; dict() propaga TypeError si el caller rompe el contrato.
# ------------------------------------------------------------
def _public_rehydrate_job(job: dict) -> dict:
    return dict(job)

# ------------------------------------------------------------
# Recibe job_id y cambios escalares y devuelve si se aplicaron al único activo.
# Muta status/progreso bajo _rehydrate_jobs_lock sin tocar last_terminal; UUID
# ajeno es False y errores excepcionales del lock/update se propagan al caller.
# ------------------------------------------------------------
async def _update_rehydrate_job(job_id: str, updates: dict) -> bool:
    global _rehydrate_active_job
    async with _rehydrate_jobs_lock:
        if not _rehydrate_active_job or _rehydrate_active_job.get("job_id") != job_id:
            return False
        _rehydrate_active_job.update(updates)
        return True

# ------------------------------------------------------------
# Recibe ámbito, política, días y request_id opcional y devuelve
# (job_id, created). Bajo _rehydrate_jobs_lock reserva/muta el estado O(1) sin
# GUIDs o reutiliza activo/último terminal idempotente; no hace I/O. Depende de
# uuid.uuid4 y _utcnow_naive, y propaga fallos excepcionales del lock/reloj.
# ------------------------------------------------------------
async def _create_rehydrate_job(
    mode: str,
    refresh_policy: str,
    older_than_days: int,
    request_id: Optional[str] = None,
) -> tuple[str, bool]:
    global _rehydrate_active_job, _rehydrate_last_terminal_job
    job_id = uuid.uuid4().hex
    now_iso = _utcnow_naive().isoformat()
    async with _rehydrate_jobs_lock:
        if (
            request_id
            and _rehydrate_last_terminal_job
            and _rehydrate_last_terminal_job.get("request_id") == request_id
        ):
            return str(_rehydrate_last_terminal_job["job_id"]), False
        if _rehydrate_active_job and _rehydrate_active_job.get("status") in {"queued", "running"}:
            return str(_rehydrate_active_job["job_id"]), False

        _rehydrate_active_job = {
            "job_id": job_id,
            "request_id": request_id,
            "status": "queued",
            "mode": mode,
            "refresh_policy": refresh_policy,
            "older_than_days": older_than_days,
            "created_at": now_iso,
            "started_at": None,
            "finished_at": None,
            "scope_total": 0,
            "total": 0,
            "remaining_count": 0,
            "processed": 0,
            "refreshed": 0,
            "updated": 0,
            "unchanged": 0,
            "partial": 0,
            "skipped": 0,
            "failed": 0,
            "skipped_fresh": 0,
            "percent_total": 0,
            "message": "En cola",
            "error": None,
            "cancel_requested": False,
        }
    return job_id, True

# ------------------------------------------------------------
# Conserva la task recibida para job_id y devuelve None mientras ese UUID siga
# activo. Muta solo _rehydrate_active_task bajo _rehydrate_jobs_lock, no cancela
# ni espera la task y hace no-op si perdió ownership; no hay errores lógicos
# esperados y los fallos del lock se propagan.
# ------------------------------------------------------------
async def _register_rehydrate_task(job_id: str, task: asyncio.Task) -> None:
    global _rehydrate_active_task
    async with _rehydrate_jobs_lock:
        if _rehydrate_active_job and _rehydrate_active_job.get("job_id") == job_id:
            _rehydrate_active_task = task

# ------------------------------------------------------------
# Recibe una task opcional, libera su referencia fuerte y devuelve None si sigue
# siendo la activa. Muta _rehydrate_active_task bajo _rehydrate_jobs_lock, hace
# no-op ante otra identidad, no altera snapshots ni cancela tareas; solo podría
# propagar un fallo excepcional del lock.
# ------------------------------------------------------------
async def _release_rehydrate_task(task: Optional[asyncio.Task]) -> None:
    global _rehydrate_active_task
    async with _rehydrate_jobs_lock:
        if _rehydrate_active_task is task:
            _rehydrate_active_task = None

# ------------------------------------------------------------
# Mueve el job activo a su único slot terminal y libera active. Recibe un
# estado terminal y métricas finales, da precedencia a una cancelación bajo el
# lock y devuelve el estado realmente publicado o None si perdió ownership.
# Muta los dos slots globales, no hace I/O y lanza ValueError si el caller pasa
# un estado no terminal; depende de REHYDRATE_TERMINAL_STATES y del lock global.
# ------------------------------------------------------------
async def _finish_rehydrate_job(job_id: str, status: str, updates: Optional[dict] = None) -> Optional[str]:
    global _rehydrate_active_job, _rehydrate_last_terminal_job
    if status not in REHYDRATE_TERMINAL_STATES:
        raise ValueError(f"Estado terminal de rehydrate inválido: {status}")

    async with _rehydrate_jobs_lock:
        if not _rehydrate_active_job or _rehydrate_active_job.get("job_id") != job_id:
            return None

        final_status = "cancelled" if _rehydrate_active_job.get("cancel_requested") else status
        if updates:
            _rehydrate_active_job.update(updates)
        _rehydrate_active_job["status"] = final_status
        _rehydrate_active_job["finished_at"] = _utcnow_naive().isoformat()
        if final_status == "cancelled":
            _rehydrate_active_job["message"] = "Rehidratación detenida por el usuario"
            _rehydrate_active_job["error"] = None

        _rehydrate_last_terminal_job = dict(_rehydrate_active_job)
        _rehydrate_active_job = None
        return final_status

# ------------------------------------------------------------
# Reclama el job_id queued y devuelve su configuración escalar o None si perdió
# ownership/fue cancelado. Bajo _rehydrate_jobs_lock muta active y timestamps; una
# cancelación previa mueve el snapshot a last_terminal. No hace I/O ni restablece
# solicitudes; depende de _utcnow_naive y solo propaga fallos excepcionales del lock.
# ------------------------------------------------------------
async def _begin_rehydrate_job(job_id: str) -> Optional[dict]:
    global _rehydrate_active_job, _rehydrate_last_terminal_job
    async with _rehydrate_jobs_lock:
        if not _rehydrate_active_job or _rehydrate_active_job.get("job_id") != job_id:
            return None
        if _rehydrate_active_job.get("cancel_requested"):
            _rehydrate_active_job.update({
                "status": "cancelled",
                "finished_at": _utcnow_naive().isoformat(),
                "message": "Rehidratación detenida antes de comenzar",
            })
            _rehydrate_last_terminal_job = dict(_rehydrate_active_job)
            _rehydrate_active_job = None
            return None

        _rehydrate_active_job.update({
            "status": "running",
            "started_at": _utcnow_naive().isoformat(),
            "message": "Seleccionando fichas",
        })
        return {
            "mode": _rehydrate_active_job["mode"],
            "refresh_policy": _rehydrate_active_job["refresh_policy"],
            "older_than_days": _rehydrate_active_job["older_than_days"],
        }

# ------------------------------------------------------------
# Comprueba job_id entre fichas y devuelve True si desapareció, perdió ownership
# o debe detenerse; False permite continuar. Bajo _rehydrate_jobs_lock, una
# cancelación muta active/last_terminal y el timestamp de _utcnow_naive; no hace
# I/O ni revierte commits y solo propaga fallos excepcionales del lock/reloj.
# ------------------------------------------------------------
async def _stop_rehydrate_if_requested(job_id: str) -> bool:
    global _rehydrate_active_job, _rehydrate_last_terminal_job
    async with _rehydrate_jobs_lock:
        if not _rehydrate_active_job or _rehydrate_active_job.get("job_id") != job_id:
            return True
        if not _rehydrate_active_job.get("cancel_requested"):
            return False

        _rehydrate_active_job.update({
            "status": "cancelled",
            "finished_at": _utcnow_naive().isoformat(),
            "message": "Rehidratación detenida por el usuario",
        })
        _rehydrate_last_terminal_job = dict(_rehydrate_active_job)
        _rehydrate_active_job = None
        return True

# ------------------------------------------------------------
# Normaliza el source_guid real de una ficha de caché para que el
# indexador vaya a la URL de detalles. Recibe una fila ya cargada, no hace
# I/O ni mutaciones y usa el GUID namespaced como fallback legacy.
# ------------------------------------------------------------
def _resolve_source_guid_for_rehydrate(torrent: TorrentCache) -> str:
    if torrent.source_guid:
        return str(torrent.source_guid).strip()
    guid = str(torrent.guid or "").strip()
    if "-" in guid:
        return guid.split("-", 1)[1].strip()
    return guid

# ------------------------------------------------------------
# Selecciona solo GUIDs escalares dentro de una Session corta y aplica el
# filtro stale en SQLite usando fecha y revisión del scraper. Recibe ámbito,
# selección efímera y umbral; devuelve scope_total, cola y skipped_fresh.
# Los errores DB se propagan al runner para marcar el job failed.
# ------------------------------------------------------------
def _select_rehydrate_guids(
    mode: str,
    wanted_guids: list[str],
    refresh_policy: str,
    older_than_days: int,
) -> tuple[int, list[str], int]:
    cutoff = _utcnow_naive() - timedelta(days=older_than_days)
    with Session(engine) as session:
        scope_query = select(TorrentCache.guid)
        if mode == "selected":
            scope_query = scope_query.where(TorrentCache.guid.in_(wanted_guids))
        else:
            scope_query = scope_query.order_by(desc(TorrentCache.published_at))
        scope_rows = [str(guid) for guid in session.exec(scope_query).all()]

        eligible_query = select(TorrentCache.guid)
        if mode == "selected":
            eligible_query = eligible_query.where(TorrentCache.guid.in_(wanted_guids))
        else:
            eligible_query = eligible_query.order_by(desc(TorrentCache.published_at))

        if refresh_policy == "stale":
            eligible_query = eligible_query.where(or_(
                TorrentCache.last_scraped_at == None,
                TorrentCache.last_scraped_at <= cutoff,
                TorrentCache.scrape_revision == None,
                TorrentCache.scrape_revision < REHYDRATE_SCRAPE_REVISION,
            ))
        eligible_rows = [str(guid) for guid in session.exec(eligible_query).all()]

    if mode == "selected":
        scope_set = set(scope_rows)
        eligible_set = set(eligible_rows)
        scope_rows = [guid for guid in wanted_guids if guid in scope_set]
        eligible_rows = [guid for guid in wanted_guids if guid in eligible_set]

    scope_total = len(scope_rows)
    return scope_total, eligible_rows, max(0, scope_total - len(eligible_rows))

# ------------------------------------------------------------
# Recibe un GUID y lee en una Session corta ficha/config/credencial, cerrando DB
# antes del await remoto. Devuelve (contexto, None) o (None, motivo), sin escribir
# ni registrar la cookie. Ausencias son errores lógicos; fallos DB/descifrado se
# propagan. Depende de _resolve_source_guid_for_rehydrate y decrypt_secret.
# ------------------------------------------------------------
def _load_rehydrate_context(guid: str) -> tuple[Optional[dict], Optional[str]]:
    with Session(engine) as session:
        torrent = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not torrent:
            return None, "La ficha ya no existe"

        indexer_id = (torrent.indexer or "").strip()
        if not indexer_id:
            return None, "La ficha no tiene indexador"

        indexer_config = session.exec(
            select(IndexerConfig).where(IndexerConfig.identifier == indexer_id)
        ).first()
        if not indexer_config:
            return None, f"El indexador '{indexer_id}' no está configurado"

        source_guid = _resolve_source_guid_for_rehydrate(torrent)
        if not source_guid:
            return None, "No se pudo resolver source_guid"

        tracker_cookie = decrypt_secret(indexer_config.cookie_string) if indexer_config.cookie_string else ""
        base_title = (torrent.tracker_title or torrent.enriched_title or torrent.original_title or "").strip()
        return {
            "guid": torrent.guid,
            "source_guid": source_guid,
            "indexer_id": indexer_id,
            "cookie_string": tracker_cookie,
            "base_title": base_title,
            "current_original_title": torrent.original_title or "",
        }, None

# ------------------------------------------------------------
# Persiste para GUID un payload tracker válido con scraped_at y devuelve
# (aceptado, modificado). Escribe/commit frescura y campos observados en SQLite,
# preservando manual/IA/TVDB/qB; rechaza con (False, False) evidencia, identidad,
# fecha o tamaño inválidos y propaga errores DB. Depende de los normalizadores,
# _resolve_source_guid_for_rehydrate y merge_tracker_metadata_json.
# ------------------------------------------------------------
def _merge_rehydrate_payload(guid: str, payload: dict, scraped_at: datetime) -> tuple[bool, bool]:
    is_structurally_valid = payload.get(
        "structurally_valid",
        payload.get("is_valid", payload.get("valid")),
    )
    scraped_from_tracker = payload.get("scraped_from_tracker")
    if is_structurally_valid is not True or scraped_from_tracker is not True:
        return False, False

    aliases = {
        "fansub_name": "fansub",
        "enriched_title": "title",
        "tracker_seeders": "seeders",
        "tracker_leechers": "leechers",
        "tracker_freeleech": "freeleech_present",
    }
    observed_aliases = {
        "fansub_name": {"fansub", "fansub_name"},
        "original_title": {"tracker_title", "original_title"},
        "enriched_title": {"tracker_title", "enriched_title", "title"},
        "screenshots_json": {"screenshots", "screenshots_json"},
        "tracker_metadata_json": {"tracker_metadata", "tracker_metadata_json"},
        "file_entries_json": {"file_entries", "file_entries_json"},
        "tracker_freeleech": {"freeleech_present", "tracker_freeleech"},
        "freeleech_until": {"freeleech_present", "freeleech_until"},
        "raw_filenames": {"file_entries", "raw_filenames"},
    }
    raw_observed_fields = payload.get("observed_fields")
    if raw_observed_fields is not None and not isinstance(
        raw_observed_fields,
        (list, tuple, set, frozenset),
    ):
        return False, False
    observed_fields = (
        {str(field_name) for field_name in raw_observed_fields}
        if isinstance(raw_observed_fields, (list, tuple, set, frozenset))
        else None
    )
    if observed_fields is not None and not {"source_guid", "tracker_title"}.issubset(observed_fields):
        return False, False

    with Session(engine) as session:
        torrent = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not torrent:
            return False, False

        expected_source_guid = _resolve_source_guid_for_rehydrate(torrent)
        incoming_source_guid = str(payload.get("source_guid") or "").strip()
        incoming_tracker_title = str(payload.get("tracker_title") or payload.get("title") or "").strip()
        incoming_size_bytes = _normalize_tracker_size_bytes(payload.get("size_bytes"))
        if not incoming_source_guid or not incoming_tracker_title or incoming_size_bytes is None:
            return False, False
        if incoming_source_guid and expected_source_guid and incoming_source_guid != expected_source_guid:
            return False, False

        changed = False
        for field_name in REHYDRATE_TRACKER_FIELDS:
            required_observations = observed_aliases.get(field_name, {field_name})
            if observed_fields is not None and observed_fields.isdisjoint(required_observations):
                continue
            payload_key = field_name if field_name in payload else aliases.get(field_name)
            if not payload_key or payload_key not in payload:
                continue
            incoming_value = payload.get(payload_key)
            clears_observed_freeleech_expiry = (
                field_name == "freeleech_until"
                and isinstance(payload.get("freeleech_present"), bool)
            )
            if incoming_value is None and not clears_observed_freeleech_expiry:
                continue
            if field_name == "size_bytes":
                incoming_value = incoming_size_bytes
            if field_name == "tracker_metadata_json":
                incoming_metadata = payload.get("tracker_metadata")
                if not isinstance(incoming_metadata, dict):
                    continue
                incoming_value = merge_tracker_metadata_json(
                    torrent.tracker_metadata_json,
                    incoming_metadata,
                )
                if incoming_value is None:
                    continue
            if field_name == "source_guid":
                incoming_value = incoming_source_guid
            if field_name in {"published_at", "freeleech_until"} and incoming_value is not None:
                incoming_value = _as_utc_naive_datetime(incoming_value)
                if incoming_value is None:
                    continue
            if getattr(torrent, field_name) != incoming_value:
                setattr(torrent, field_name, incoming_value)
                changed = True

        incoming_revision = payload.get("scrape_revision", REHYDRATE_SCRAPE_REVISION)
        try:
            normalized_revision = max(REHYDRATE_SCRAPE_REVISION, int(incoming_revision))
        except (TypeError, ValueError):
            normalized_revision = REHYDRATE_SCRAPE_REVISION
        if torrent.scrape_revision != normalized_revision:
            torrent.scrape_revision = normalized_revision
            changed = True

        normalized_scraped_at = _as_utc_naive_datetime(scraped_at)
        if normalized_scraped_at is None:
            return False, False
        torrent.last_scraped_at = normalized_scraped_at
        session.add(torrent)
        session.commit()
        return True, changed

# ------------------------------------------------------------
# Publica para job_id los contadores, processed y total y devuelve None. Calcula
# progreso total y muta solo el job propietario mediante _update_rehydrate_job;
# perder ownership es un no-op y los errores excepcionales del lock se propagan.
# No conserva lotes ni cola duplicada porque la WebUI solo consume el total.
# ------------------------------------------------------------
async def _record_rehydrate_progress(job_id: str, counters: dict, processed: int, total: int) -> None:
    percent_total = int((processed / total) * 100) if total else 100
    await _update_rehydrate_job(job_id, {
        **counters,
        "processed": processed,
        "remaining_count": max(0, total - processed),
        "percent_total": percent_total,
        "message": f"Procesando ficha {processed}/{total}",
    })

# ------------------------------------------------------------
# Ejecuta secuencialmente un job con un AsyncClient compartido, sesiones DB
# cortas alrededor del I/O y commit independiente por ficha. Recibe UUID y
# selección efímera y no devuelve datos; realiza red/DB, actualiza progreso y
# cancelación, aísla fallos por ficha y convierte un fallo global en terminal
# failed. Depende del indexer, helpers de lifecycle/merge y siempre cierra el
# cliente/libera la referencia fuerte de su asyncio.Task.
# ------------------------------------------------------------
async def _run_rehydrate_job(job_id: str, wanted_guids: list[str]) -> None:
    current_task = asyncio.current_task()
    try:
        job_config = await _begin_rehydrate_job(job_id)
        if not job_config:
            return

        mode = job_config["mode"]
        refresh_policy = job_config["refresh_policy"]
        older_than_days = job_config["older_than_days"]
        scope_total, guid_queue, skipped_fresh = _select_rehydrate_guids(
            mode,
            wanted_guids,
            refresh_policy,
            older_than_days,
        )
        total = len(guid_queue)
        await _update_rehydrate_job(job_id, {
            "scope_total": scope_total,
            "total": total,
            "remaining_count": total,
            "skipped_fresh": skipped_fresh,
            "message": "Procesando fichas" if total else "No hay fichas elegibles",
        })

        if total == 0:
            await _finish_rehydrate_job(job_id, "completed", {
                "message": "No hay fichas antiguas que actualizar" if refresh_policy == "stale" else "No hay fichas que actualizar",
                "percent_total": 100,
                "remaining_count": 0,
            })
            return

        counters = {
            "refreshed": 0,
            "updated": 0,
            "unchanged": 0,
            "partial": 0,
            "skipped": 0,
            "failed": 0,
        }

        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as shared_client:
            for position, guid in enumerate(guid_queue, start=1):
                if await _stop_rehydrate_if_requested(job_id):
                    logger.warning(
                        f"⏹️ [CACHE] [REHYDRATE] Detenido por usuario | "
                        f"procesadas={position - 1} | restantes={total - position + 1}"
                    )
                    return

                context_load_failed = False
                try:
                    context, skip_reason = _load_rehydrate_context(guid)
                except Exception as exc:
                    context = None
                    skip_reason = None
                    context_load_failed = True
                    counters["failed"] += 1
                    logger.error(f"❌ [CACHE] [REHYDRATE] Error preparando ficha {guid}: {exc}")

                if not context:
                    if not context_load_failed:
                        counters["skipped"] += 1
                        logger.warning(f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={guid} | Motivo='{skip_reason}'.")
                else:
                    indexer = indexer_manager.get_indexer(context["indexer_id"])
                    if not indexer or not hasattr(indexer, "rehydrate_torrent"):
                        counters["skipped"] += 1
                        logger.warning(
                            f"⚠️ [CACHE] [REHYDRATE] SKIP | ID={guid} | "
                            f"Motivo='Indexador sin soporte de rehidratación: {context['indexer_id']}'."
                        )
                    else:
                        try:
                            payload = await indexer.rehydrate_torrent(
                                source_guid=context["source_guid"],
                                cookie_string=context["cookie_string"],
                                base_title=context["base_title"],
                                current_original_title=context["current_original_title"],
                                client=shared_client,
                            )
                            await asyncio.sleep(0.5)

                            if not isinstance(payload, dict) or not payload:
                                counters["failed"] += 1
                                logger.error(
                                    f"❌ [CACHE] [REHYDRATE] MISS | ID={guid} | "
                                    f"source_guid={context['source_guid']}"
                                )
                            else:
                                persisted, changed = _merge_rehydrate_payload(
                                    guid,
                                    payload,
                                    _utcnow_naive(),
                                )
                                if not persisted:
                                    counters["failed"] += 1
                                    logger.error(
                                        f"❌ [CACHE] [REHYDRATE] INVALID | ID={guid} | "
                                        "La ficha no superó la validación estructural o desapareció antes del commit."
                                    )
                                else:
                                    warnings = payload.get("warnings") or []
                                    is_partial = bool(payload.get("partial")) or bool(warnings)
                                    counters["refreshed"] += 1
                                    if is_partial:
                                        counters["partial"] += 1
                                    elif changed:
                                        counters["updated"] += 1
                                    else:
                                        counters["unchanged"] += 1
                                    logger.info(
                                        f"♻️ [CACHE] [REHYDRATE] HIT | ID={guid} | "
                                        f"cambios={'sí' if changed else 'no'} | parcial={'sí' if is_partial else 'no'}"
                                    )
                        except Exception as exc:
                            counters["failed"] += 1
                            logger.error(f"❌ [CACHE] [REHYDRATE] Error en ficha {guid}: {exc}")

                await _record_rehydrate_progress(job_id, counters, position, total)

        final_status = await _finish_rehydrate_job(job_id, "completed", {
            **counters,
            "processed": total,
            "message": "Rehidratación completada",
            "percent_total": 100,
            "remaining_count": 0,
        })
        logger.info(
            f"✅ [CACHE] [REHYDRATE] Proceso {final_status or 'finalizado'} | modo={mode} | "
            f"procesadas={total} | refrescadas={counters['refreshed']} | "
            f"actualizadas={counters['updated']} | sin_cambios={counters['unchanged']} | "
            f"parciales={counters['partial']} | omitidas={counters['skipped']} | "
            f"fallidas={counters['failed']}"
        )
    except Exception as exc:
        await _finish_rehydrate_job(job_id, "failed", {
            "error": "Error interno durante la rehidratación.",
            "message": "Error durante la rehidratación",
        })
        logger.error(f"❌ [CACHE] [REHYDRATE] Error crítico en job {job_id}: {exc}")
    finally:
        await _release_rehydrate_task(current_task)

# ------------------------------------------------------------
# Valida RehydrateForm y devuelve {success, job_id, created} o error lógico. Puede
# reservar el slot global, crear el runner con asyncio.create_task y registrarlo;
# la selección solo vive en la task. Depende de _create_rehydrate_job,
# _run_rehydrate_job y _register_rehydrate_task; request_id deduplica respuestas
# perdidas, entradas inválidas no mutan estado y errores internos se propagan.
# ------------------------------------------------------------
@router.post("/cache/rehydrate")
async def start_rehydrate_cache_entries(data: RehydrateForm):
    mode = (data.mode or "").strip().lower()
    if mode not in ["selected", "all"]:
        return {"success": False, "error": "Modo inválido. Usa 'selected' o 'all'."}
    if mode == "selected" and not data.guids:
        return {"success": False, "error": "No se han recibido GUIDs para rehidratar."}

    refresh_policy = (data.refresh_policy or "complete").strip().lower()
    if refresh_policy not in {"stale", "complete"}:
        return {"success": False, "error": "Política inválida. Usa 'stale' o 'complete'."}

    older_than_days = data.older_than_days if data.older_than_days is not None else 1
    if older_than_days < 1 or older_than_days > 3650:
        return {"success": False, "error": "older_than_days debe ser un entero entre 1 y 3650."}

    request_id = (data.request_id or "").strip() or None
    if request_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        return {
            "success": False,
            "error": "request_id debe contener entre 8 y 64 caracteres alfanuméricos, '-' o '_'.",
        }

    if mode == "selected":
        wanted_guids = list(dict.fromkeys(
            str(guid).strip() for guid in (data.guids or []) if str(guid).strip()
        ))
        if not wanted_guids:
            return {"success": False, "error": "No se han recibido GUIDs válidos para rehidratar."}
    else:
        wanted_guids = []

    job_id, created = await _create_rehydrate_job(
        mode,
        refresh_policy,
        older_than_days,
        request_id=request_id,
    )
    if created:
        task = asyncio.create_task(_run_rehydrate_job(job_id, wanted_guids))
        await _register_rehydrate_task(job_id, task)
    return {"success": True, "job_id": job_id, "created": created}

# ------------------------------------------------------------
# Sin entradas, devuelve {success, active, job} para reenganchar la WebUI. Lee el
# slot bajo _rehydrate_jobs_lock sin crear/mutar trabajo ni hacer I/O; active=false
# es normal. Depende de _public_rehydrate_job y propaga fallos excepcionales del lock.
# ------------------------------------------------------------
@router.get("/cache/rehydrate/active")
async def get_active_rehydrate_job():
    async with _rehydrate_jobs_lock:
        snapshot = _public_rehydrate_job(_rehydrate_active_job) if _rehydrate_active_job else None
    return {"success": True, "active": snapshot is not None, "job": snapshot}

# ------------------------------------------------------------
# Recibe job_id y devuelve success+snapshot desde active/last_terminal o un error
# not-found tipado. Lee bajo _rehydrate_jobs_lock sin mutar lifecycle ni hacer I/O;
# depende de _public_rehydrate_job y propaga fallos excepcionales del lock/copia.
# ------------------------------------------------------------
@router.get("/cache/rehydrate/{job_id}/status")
async def get_rehydrate_job_status(job_id: str):
    async with _rehydrate_jobs_lock:
        job = None
        if _rehydrate_active_job and _rehydrate_active_job.get("job_id") == job_id:
            job = _rehydrate_active_job
        elif _rehydrate_last_terminal_job and _rehydrate_last_terminal_job.get("job_id") == job_id:
            job = _rehydrate_last_terminal_job
        if not job:
            return {
                "success": False,
                "code": "rehydrate_job_not_found",
                "error": "Job de rehidratación no encontrado.",
            }
        snapshot = _public_rehydrate_job(job)
    return {"success": True, "job": snapshot}

# ------------------------------------------------------------
# Recibe job_id y devuelve un dict success/error con snapshot cuando corresponde.
# Bajo _rehydrate_jobs_lock muta cancel_requested y, si queued, active/last_terminal
# y timestamps; running conserva el slot hasta el siguiente punto seguro. Repetir
# un terminal cancelled es idempotente, UUID/estado ajenos son errores lógicos,
# y depende de _utcnow_naive, _public_rehydrate_job y logger.
# ------------------------------------------------------------
@router.post("/cache/rehydrate/{job_id}/cancel")
async def cancel_rehydrate_job(job_id: str):
    global _rehydrate_active_job, _rehydrate_last_terminal_job
    async with _rehydrate_jobs_lock:
        if _rehydrate_active_job and _rehydrate_active_job.get("job_id") == job_id:
            status = _rehydrate_active_job.get("status")
            if status == "queued":
                _rehydrate_active_job.update({
                    "status": "cancelled",
                    "cancel_requested": True,
                    "finished_at": _utcnow_naive().isoformat(),
                    "message": "Rehidratación detenida antes de comenzar",
                })
                _rehydrate_last_terminal_job = dict(_rehydrate_active_job)
                _rehydrate_active_job = None
                terminal = True
            elif status == "running":
                already_requested = bool(_rehydrate_active_job.get("cancel_requested"))
                _rehydrate_active_job["cancel_requested"] = True
                _rehydrate_active_job["message"] = "Deteniendo después de la ficha actual"
                terminal = False
            else:
                return {"success": False, "error": "El job ya no admite cancelación."}
        elif (
            _rehydrate_last_terminal_job
            and _rehydrate_last_terminal_job.get("job_id") == job_id
            and _rehydrate_last_terminal_job.get("status") == "cancelled"
        ):
            return {"success": True, "status": "cancelled", "already_cancelled": True}
        else:
            return {"success": False, "error": "Job de rehidratación no encontrado o ya finalizado."}

    logger.warning(f"⏹️ [CACHE] [REHYDRATE] Solicitud de detención recibida | job_id={job_id}")
    return {
        "success": True,
        "status": "cancelled" if terminal else "stopping",
        "already_requested": already_requested if not terminal else False,
    }

# ------------------------------------------------------------
# Empareja una ficha con un hash 40/64 hexadecimal y, si hay configuración qB,
# consulta telemetría fuera de SQLite antes de un commit corto. Devuelve success y
# warning si el vínculo se guardó pero qB falló; solo una respuesta válida vacía
# aplica not_found. Depende de _load_qbittorrent_access, cliente qB y apply helper.
# ------------------------------------------------------------
@router.post("/torrent/pair")
async def pair_torrent(data: PairTorrentForm):
    info_hash = (data.info_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", info_hash):
        return {"success": False, "error": "El info hash debe tener 40 o 64 caracteres hexadecimales."}

    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == data.guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado en la base de datos."}
        sys_config, password, qb_error = _load_qbittorrent_access(session)

    telemetry = None
    telemetry_observed = False
    warning = qb_error
    if sys_config and password is not None and not qb_error:
        try:
            telemetry = await get_torrent_telemetry(
                sys_config.qbittorrent_url,
                sys_config.qbittorrent_user,
                password,
                info_hash,
            )
            telemetry_observed = True
        except QBittorrentError as exc:
            warning = str(exc)
            logger.warning(f"⚠️ qBittorrent no devolvió telemetría al emparejar {data.guid}: {exc}")

    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == data.guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado en la base de datos."}
        t.info_hash = info_hash
        if telemetry_observed:
            _apply_telemetry_to_torrent(t, telemetry)
        session.add(t)
        session.commit()
    logger.info(f"🔗 Torrent {data.guid} emparejado manualmente con hash {info_hash}")
    return {"success": True, "warning": warning}


# ------------------------------------------------------------
# Refresca por GUID la telemetría qB. Lee ficha/config, cierra SQLite durante el
# GET y persiste después una observación válida; None confirmado marca not_found,
# mientras auth/red/HTTP retorna success=false y conserva el último estado. Devuelve
# métricas normalizadas y depende del cliente qB y _apply_telemetry_to_torrent.
# ------------------------------------------------------------
@router.get("/torrent/{guid}/telemetry")
async def get_torrent_client_telemetry(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado."}
        if not t.info_hash:
            return {"success": False, "error": "La ficha no tiene info_hash vinculado."}

        info_hash = t.info_hash
        sys_config, password, qb_error = _load_qbittorrent_access(session)
        if qb_error:
            return {"success": False, "error": qb_error}

    try:
        telemetry = await get_torrent_telemetry(
            sys_config.qbittorrent_url,
            sys_config.qbittorrent_user,
            password,
            info_hash,
        )
    except QBittorrentError as exc:
        logger.warning(f"⚠️ qBittorrent no actualizó telemetría para {guid}: {exc}")
        return {"success": False, "error": str(exc)}

    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado."}
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
# Descarga metainfo y calcula/persiste su info hash para clientes legacy del API.
# Recibe GUID y devuelve hash o error lógico; separa lecturas/escritura SQLite del
# I/O tracker, no altera qBittorrent y conserva compatibilidad aunque la WebUI ya
# no exponga esta acción. Depende del indexer y calculate_metainfo_hash.
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
        info_hash = calculate_metainfo_hash(torrent_bytes)
    except Exception as exc:
        logger.error(f"No se pudo calcular Info Hash para {guid}: {type(exc).__name__}")
        return {"success": False, "error": "No se pudo descargar o procesar el .torrent origen."}

    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado."}
        t.info_hash = info_hash
        session.add(t)
        session.commit()

    logger.info(f"Info Hash calculado manualmente para {guid}: {info_hash}")
    return {"success": True, "info_hash": info_hash}


# ------------------------------------------------------------
# Envía el metainfo original de una ficha a qBittorrent. Recibe GUID y devuelve
# hash, telemetría y si ya existía; lee tracker/qB en una Session corta, descarga
# fuera de SQLite, calcula/persiste hash, añade multipart si falta y verifica su
# presencia con reintentos acotados. Muta DB y qB, conserva bytes info intactos y
# devuelve error seguro ante configuración, tracker o WebAPI. Depende del manager,
# add_torrent, get_torrent_telemetry y calculate_metainfo_hash.
# ------------------------------------------------------------
@router.post("/torrent/{guid}/send_to_client")
async def send_torrent_to_client(guid: str):
    with Session(engine) as session:
        torrent = session.exec(
            select(TorrentCache).where(TorrentCache.guid == guid)
        ).first()
        if not torrent:
            return {"success": False, "error": "Torrent no encontrado."}

        indexer = indexer_manager.get_indexer(torrent.indexer)
        if not indexer:
            return {
                "success": False,
                "error": f"El indexador '{torrent.indexer}' no esta disponible.",
            }
        indexer_config = session.exec(
            select(IndexerConfig).where(IndexerConfig.identifier == torrent.indexer)
        ).first()
        tracker_cookie = (
            decrypt_secret(indexer_config.cookie_string)
            if indexer_config and indexer_config.cookie_string
            else ""
        )
        source_guid = torrent.source_guid or (
            guid.split("-", 1)[1] if "-" in guid else guid
        )
        qb_config, qb_password, qb_error = _load_qbittorrent_access(session)
        if qb_error:
            return {"success": False, "error": qb_error}
        qb_url = qb_config.qbittorrent_url
        qb_user = qb_config.qbittorrent_user

    try:
        torrent_bytes = await indexer.download_torrent(source_guid, tracker_cookie)
        info_hash = calculate_metainfo_hash(torrent_bytes)
    except Exception as exc:
        logger.error(
            f"No se pudo preparar el envío a qBittorrent para {guid}: {type(exc).__name__}"
        )
        return {
            "success": False,
            "error": "No se pudo descargar o procesar el .torrent origen.",
        }

    with Session(engine) as session:
        torrent = session.exec(
            select(TorrentCache).where(TorrentCache.guid == guid)
        ).first()
        if not torrent:
            return {"success": False, "error": "Torrent no encontrado."}
        torrent.info_hash = info_hash
        session.add(torrent)
        session.commit()

    try:
        telemetry = await get_torrent_telemetry(
            qb_url,
            qb_user,
            qb_password,
            info_hash,
        )
        already_in_client = telemetry is not None
        if telemetry is None:
            await add_torrent(
                qb_url,
                qb_user,
                qb_password,
                torrent_bytes,
                f"{guid}.torrent",
            )
            for delay in (0.0, 0.1, 0.2, 0.4, 0.8, 1.0):
                if delay:
                    await asyncio.sleep(delay)
                telemetry = await get_torrent_telemetry(
                    qb_url,
                    qb_user,
                    qb_password,
                    info_hash,
                )
                if telemetry is not None:
                    break
    except QBittorrentError as exc:
        logger.warning(f"⚠️ qBittorrent rechazó el envío de {guid}: {exc}")
        return {
            "success": False,
            "error": str(exc),
            "info_hash": info_hash,
        }

    if telemetry is None:
        logger.warning(
            f"⚠️ qBittorrent aceptó la petición de {guid}, pero no publicó el hash esperado."
        )
        return {
            "success": False,
            "error": "qBittorrent aceptó la petición, pero el torrent no apareció en el cliente.",
            "info_hash": info_hash,
        }

    with Session(engine) as session:
        torrent = session.exec(
            select(TorrentCache).where(TorrentCache.guid == guid)
        ).first()
        if not torrent:
            return {"success": False, "error": "Torrent no encontrado."}
        torrent.info_hash = info_hash
        _apply_telemetry_to_torrent(torrent, telemetry)
        session.add(torrent)
        session.commit()

    action_label = (
        "ya estaba presente en" if already_in_client else "fue enviado a"
    )
    logger.info(f"📥 Torrent {guid} {action_label} qBittorrent con hash {info_hash}.")
    return {
        "success": True,
        "info_hash": info_hash,
        "already_in_client": already_in_client,
        "telemetry": telemetry,
    }
