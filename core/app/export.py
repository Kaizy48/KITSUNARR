import asyncio
from datetime import datetime

from fastapi.encoders import jsonable_encoder
from sqlmodel import Session, select

from core.database.engine import engine
from core.app.logger import logger
from core.app.background import pause_worker, resume_worker
from core.database.models import TorrentCache, TVDBCache, TVDBEpisodes, TorrentTVDBCandidates

IMPORT_SCHEMA_VERSION = "0.5.0"
IMPORT_BATCH_SIZE = 75
IMPORT_LOCK = asyncio.Lock()

TORRENT_REHYDRATED_FIELDS = {
    "exists_in_client",
    "client_status",
    "progress",
    "download_speed",
    "upload_speed",
    "eta",
}

# ------------------------------------------------------------
# Convierte fechas importadas a objetos datetime validos para que
# Kitsunarr pueda restaurar campos temporales de cache y TVDB.
# ------------------------------------------------------------
def safe_parse_datetime(date_str: str | None):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

# ------------------------------------------------------------
# Prepara una ficha torrent para exportacion eliminando datos que
# dependen del estado vivo del cliente qBittorrent de esta instalacion.
# ------------------------------------------------------------
def sanitize_torrent_for_export(torrent: TorrentCache) -> dict:
    data = jsonable_encoder(torrent)
    for field in TORRENT_REHYDRATED_FIELDS:
        data.pop(field, None)
    return data

# ------------------------------------------------------------
# Reconstruye una ficha TorrentCache importada y adapta su enlace de
# descarga a la instalacion actual y reinicia la telemetria viva.
# ------------------------------------------------------------
def rehydrate_torrent_from_import(data: dict, base_url: str) -> TorrentCache:
    data = dict(data)
    data["pub_date"] = str(data["pub_date"]) if data.get("pub_date") else None
    data["freeleech_until"] = safe_parse_datetime(data.get("freeleech_until"))

    for unknown_key in ("added_at", "updated_at", "is_freeleech", "ratio"):
        data.pop(unknown_key, None)

    if data.get("guid"):
        data["download_url"] = f"{base_url}/api/download/{data['guid']}_base"

    data["exists_in_client"] = False
    data["client_status"] = "unknown"
    data["progress"] = 0.0
    data["download_speed"] = 0
    data["upload_speed"] = 0
    data["eta"] = 8640000

    valid_fields = {f for f in TorrentCache.__fields__}
    data = {k: v for k, v in data.items() if k in valid_fields}
    return TorrentCache(**data)

# ------------------------------------------------------------
# Reconstruye una ficha maestra TVDB importada y normaliza su fecha
# de actualizacion para guardarla en la biblioteca local.
# ------------------------------------------------------------
def rehydrate_tvdb_from_import(data: dict) -> TVDBCache:
    data = dict(data)
    parsed_last_updated = safe_parse_datetime(data.get("last_updated"))
    data["last_updated"] = (parsed_last_updated or datetime.utcnow()).isoformat()
    valid_fields = {f for f in TVDBCache.__fields__}
    data = {k: v for k, v in data.items() if k in valid_fields}
    return TVDBCache(**data)

# ------------------------------------------------------------
# Prepara un episodio TVDB importado eliminando claves locales y
# filtrando campos desconocidos antes de insertarlo en Kitsunarr.
# ------------------------------------------------------------
def rehydrate_episode_from_import(data: dict) -> TVDBEpisodes:
    data = dict(data)
    data.pop("id", None)
    valid_fields = {f for f in TVDBEpisodes.__fields__}
    data = {k: v for k, v in data.items() if k in valid_fields}
    return TVDBEpisodes(**data)

# ------------------------------------------------------------
# Prepara una relacion torrent-TVDB importada dejando solo las claves
# que forman el vinculo local de Kitsunarr.
# ------------------------------------------------------------
def rehydrate_candidate_from_import(data: dict) -> TorrentTVDBCandidates:
    data = dict(data)
    valid_fields = {f for f in TorrentTVDBCandidates.__fields__}
    data = {k: v for k, v in data.items() if k in valid_fields}
    return TorrentTVDBCandidates(**data)

# ------------------------------------------------------------
# Envuelve un paquete exportado con metadatos de version, conteos y
# aviso de que los secretos nunca viajan dentro del JSON.
# ------------------------------------------------------------
def build_export_payload(export_type: str, content: dict) -> dict:
    payload = {
        "type": export_type,
        "version": IMPORT_SCHEMA_VERSION,
        "exported_at": datetime.utcnow().isoformat(),
        "generator": "kitsunarr",
        "sensitive_data": "excluded",
        "rehydrated_on_import": sorted(TORRENT_REHYDRATED_FIELDS),
        **content,
    }
    payload["counts"] = {
        "torrents": len(payload.get("torrents", [])),
        "shows": len(payload.get("shows", [])),
        "episodes": len(payload.get("episodes", [])),
        "candidates": len(payload.get("candidates", [])),
    }
    return payload

# ------------------------------------------------------------
# Comprueba que el paquete de importacion tenga listas validas para
# que Kitsunarr pueda procesarlas por lotes sin acaparar la base.
# ------------------------------------------------------------
def validate_import_payload(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("El archivo no tiene el formato de exportacion de Kitsunarr.")

    for key in ("torrents", "shows", "episodes", "candidates"):
        value = data.get(key, [])
        if value is None:
            data[key] = []
            value = []
        if not isinstance(value, list):
            raise ValueError(f"El bloque '{key}' no es una lista valida.")

# ------------------------------------------------------------
# Exporta unicamente la cache de torrents de Kitsunarr para copias
# parciales, revision externa o migraciones ligeras.
# ------------------------------------------------------------
def export_torrents_only(session: Session) -> dict:
    torrents = session.exec(select(TorrentCache)).all()
    return build_export_payload("torrents_only", {
        "torrents": [sanitize_torrent_for_export(t) for t in torrents]
    })

# ------------------------------------------------------------
# Exporta unicamente la biblioteca local de TheTVDB con sus series y
# episodios descargados.
# ------------------------------------------------------------
def export_tvdb_only(session: Session) -> dict:
    shows = session.exec(select(TVDBCache)).all()
    episodes = session.exec(select(TVDBEpisodes)).all()
    return build_export_payload("tvdb_only", {
        "shows": [jsonable_encoder(s) for s in shows],
        "episodes": [jsonable_encoder(e) for e in episodes]
    })

# ------------------------------------------------------------
# Exporta un paquete completo verificado con torrents validados,
# fichas TVDB, episodios y candidatos relacionados.
# ------------------------------------------------------------
def export_full_bundle(session: Session) -> dict:
    valid_torrents = session.exec(
        select(TorrentCache).where(
            TorrentCache.ai_status == "Listo",
            TorrentCache.tvdb_status == "Listo"
        )
    ).all()
    valid_tvdb_ids = {t.tvdb_id for t in valid_torrents if t.tvdb_id}

    shows = session.exec(
        select(TVDBCache).where(TVDBCache.tvdb_id.in_(valid_tvdb_ids))
    ).all() if valid_tvdb_ids else []

    episodes = session.exec(
        select(TVDBEpisodes).where(TVDBEpisodes.tvdb_id.in_(valid_tvdb_ids))
    ).all() if valid_tvdb_ids else []

    valid_guids = {t.guid for t in valid_torrents}
    candidates = session.exec(
        select(TorrentTVDBCandidates).where(
            TorrentTVDBCandidates.torrent_guid.in_(valid_guids)
        )
    ).all() if valid_guids else []

    return build_export_payload("full_bundle", {
        "torrents": [sanitize_torrent_for_export(t) for t in valid_torrents],
        "shows": [jsonable_encoder(s) for s in shows],
        "episodes": [jsonable_encoder(e) for e in episodes],
        "candidates": [jsonable_encoder(c) for c in candidates]
    })

# ------------------------------------------------------------
# Importa datos relacionales de Kitsunarr por lotes, evitando duplicar
# registros existentes y restaurando relaciones entre torrents y TVDB.
# ------------------------------------------------------------
async def import_relational_data(data: dict, base_url: str) -> dict:
    imported_counts = {"shows": 0, "episodes": 0, "torrents": 0, "candidates": 0}
    validate_import_payload(data)

    async with IMPORT_LOCK:
        pause_worker("importacion de respaldo")
        logger.info("📦 Iniciando proceso de importacion por lotes...")

        try:
            if "shows" in data:
                for i in range(0, len(data["shows"]), IMPORT_BATCH_SIZE):
                    batch = data["shows"][i:i + IMPORT_BATCH_SIZE]
                    ids = [str(show.get("tvdb_id")) for show in batch if show.get("tvdb_id")]
                    with Session(engine) as session:
                        existing_ids = set(session.exec(
                            select(TVDBCache.tvdb_id).where(TVDBCache.tvdb_id.in_(ids))
                        ).all()) if ids else set()
                        for show_data in batch:
                            tvdb_id = str(show_data.get("tvdb_id") or "")
                            if tvdb_id and tvdb_id not in existing_ids:
                                session.add(rehydrate_tvdb_from_import(show_data))
                                existing_ids.add(tvdb_id)
                                imported_counts["shows"] += 1
                        session.commit()
                    await asyncio.sleep(0.02)

            if "episodes" in data:
                for i in range(0, len(data["episodes"]), IMPORT_BATCH_SIZE):
                    batch = data["episodes"][i:i + IMPORT_BATCH_SIZE]
                    tvdb_ids = {str(ep.get("tvdb_id")) for ep in batch if ep.get("tvdb_id")}
                    episode_ids = [int(ep.get("episode_id")) for ep in batch if ep.get("episode_id")]
                    with Session(engine) as session:
                        existing_shows = set(session.exec(
                            select(TVDBCache.tvdb_id).where(TVDBCache.tvdb_id.in_(tvdb_ids))
                        ).all()) if tvdb_ids else set()
                        existing_episode_ids = set(session.exec(
                            select(TVDBEpisodes.episode_id).where(TVDBEpisodes.episode_id.in_(episode_ids))
                        ).all()) if episode_ids else set()
                        for ep_data in batch:
                            tvdb_id = str(ep_data.get("tvdb_id") or "")
                            episode_id = ep_data.get("episode_id")
                            if tvdb_id in existing_shows and episode_id and int(episode_id) not in existing_episode_ids:
                                session.add(rehydrate_episode_from_import(ep_data))
                                existing_episode_ids.add(int(episode_id))
                                imported_counts["episodes"] += 1
                        session.commit()
                    await asyncio.sleep(0.02)

            if "torrents" in data:
                for i in range(0, len(data["torrents"]), IMPORT_BATCH_SIZE):
                    batch = data["torrents"][i:i + IMPORT_BATCH_SIZE]
                    guids = [str(t.get("guid")) for t in batch if t.get("guid")]
                    tvdb_ids = {str(t.get("tvdb_id")) for t in batch if t.get("tvdb_id")}
                    with Session(engine) as session:
                        existing_guids = set(session.exec(
                            select(TorrentCache.guid).where(TorrentCache.guid.in_(guids))
                        ).all()) if guids else set()
                        existing_shows = set(session.exec(
                            select(TVDBCache.tvdb_id).where(TVDBCache.tvdb_id.in_(tvdb_ids))
                        ).all()) if tvdb_ids else set()
                        for t_data in batch:
                            guid = str(t_data.get("guid") or "")
                            if guid and guid not in existing_guids:
                                if t_data.get("tvdb_id") and str(t_data["tvdb_id"]) not in existing_shows:
                                    t_data = dict(t_data)
                                    t_data["tvdb_id"] = None
                                    t_data["tvdb_status"] = "Pendiente"
                                session.add(rehydrate_torrent_from_import(t_data, base_url))
                                existing_guids.add(guid)
                                imported_counts["torrents"] += 1
                        session.commit()
                    await asyncio.sleep(0.02)

            if "candidates" in data:
                for i in range(0, len(data["candidates"]), IMPORT_BATCH_SIZE):
                    batch = data["candidates"][i:i + IMPORT_BATCH_SIZE]
                    guids = {str(c.get("torrent_guid")) for c in batch if c.get("torrent_guid")}
                    tvdb_ids = {str(c.get("tvdb_id")) for c in batch if c.get("tvdb_id")}
                    with Session(engine) as session:
                        existing_torrents = set(session.exec(
                            select(TorrentCache.guid).where(TorrentCache.guid.in_(guids))
                        ).all()) if guids else set()
                        existing_shows = set(session.exec(
                            select(TVDBCache.tvdb_id).where(TVDBCache.tvdb_id.in_(tvdb_ids))
                        ).all()) if tvdb_ids else set()
                        existing_link_rows = session.exec(
                            select(TorrentTVDBCandidates).where(
                                TorrentTVDBCandidates.torrent_guid.in_(guids),
                                TorrentTVDBCandidates.tvdb_id.in_(tvdb_ids)
                            )
                        ).all() if guids and tvdb_ids else []
                        existing_links = {
                            (link.torrent_guid, link.tvdb_id)
                            for link in existing_link_rows
                        }
                        for cand_data in batch:
                            torrent_guid = str(cand_data.get("torrent_guid") or "")
                            tvdb_id = str(cand_data.get("tvdb_id") or "")
                            link_key = (torrent_guid, tvdb_id)
                            if (
                                torrent_guid in existing_torrents
                                and tvdb_id in existing_shows
                                and link_key not in existing_links
                            ):
                                session.add(rehydrate_candidate_from_import(cand_data))
                                existing_links.add(link_key)
                                imported_counts["candidates"] += 1
                        session.commit()
                    await asyncio.sleep(0.02)

            logger.info(f"📦 Importacion finalizada: {imported_counts}")
        finally:
            resume_worker("importacion de respaldo completada")
    return {"counts": imported_counts}
