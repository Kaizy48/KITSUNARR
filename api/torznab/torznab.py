import re
import json
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html import escape
from fastapi import APIRouter, Request, Response
from sqlmodel import Session, select, or_
from typing import Optional

from core.database.engine import engine
from core.database.models import SystemConfig, IndexerConfig, TorrentCache, TVDBCache
from core.app.logger import logger
from core.app.background import wake_worker, is_worker_paused
from core.app.encrypt import decrypt_secret
from core.app.indexers.manager import indexer_manager
from core.app.indexers.snapshots import merge_tracker_metadata_json

router = APIRouter(prefix="/api", tags=["Torznab"])

_TECH_BLOCK_PATTERN = re.compile(
    r"(?i)(1080p|720p|2160p|1440p|4k|8k|x264|x265|h\.?264|h\.?265|hevc|av1|"
    r"blu-?ray|bdrip|bdremux|web-?dl|webrip|hdtv|dvd|audio\s*:|subs?\s*:|"
    r"flac|aac|ac3|dts|eac3|mp3|opus|mkv|mp4|avi|softsubs|hardsubs|hdr|dv)"
)

# ------------------------------------------------------------
# Devuelve el instante UTC actual sin tzinfo para compararlo/persistirlo según
# la convención SQLite del proyecto. No recibe entradas ni tiene efectos/I/O,
# no lanza errores esperados y evita datetime.utcnow(), deprecado en Python 3.12.
# ------------------------------------------------------------
def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------
# Extrae temporadas normalizadas desde el título que Kitsunarr
# enviará a Sonarr para que pueda aplicar filtros de temporada.
# ------------------------------------------------------------
def _extract_seasons(title: str) -> list[int]:
    seasons = []
    range_match = re.search(r"S(\d{1,2})-S(\d{1,2})", title, re.IGNORECASE)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end and (end - start) < 20:
            seasons = list(range(start, end + 1))
    else:
        single_match = re.search(r"S(\d{1,2})", title, re.IGNORECASE)
        if single_match: seasons = [int(single_match.group(1))]
    return seasons


# ------------------------------------------------------------
# Extrae episodios normalizados desde el título que Kitsunarr
# enviará por Torznab cuando Sonarr solicita episodios concretos.
# ------------------------------------------------------------
def _extract_episodes(title: str) -> list[int]:
    episodes = []
    for m in re.finditer(r"(?i)E(\d{1,3})", title or ""):
        try:
            episodes.append(int(m.group(1)))
        except Exception:
            continue
    return sorted(set(episodes))


# ------------------------------------------------------------
# Detecta bloques técnicos relevantes del título para conservar
# calidad, codec, formato, audio y subtítulos en la respuesta final.
# ------------------------------------------------------------
def _extract_technical_blocks(title: str) -> list[str]:
    blocks = re.findall(r"\[[^\]]+\]", title or "")
    return [b for b in blocks if _TECH_BLOCK_PATTERN.search(b)]


# ------------------------------------------------------------
# Compone el título final que recibe Sonarr usando la normalización
# de IA/TVDB y preservando los metadatos técnicos del tracker.
# ------------------------------------------------------------
def _compose_sonarr_title(ai_title: str, enriched_title: str, tvdb_id: str = None) -> str:
    base = (ai_title or enriched_title or "").strip()
    if not base:
        return ""

    if tvdb_id and not re.search(rf"(?i)tvdb\s*[-:_ ]\s*{re.escape(str(tvdb_id))}", base):
        base = f"{base} [tvdb-{tvdb_id}]".strip()

    base_blocks = {b.lower() for b in _extract_technical_blocks(base)}
    source_blocks = _extract_technical_blocks(enriched_title or "")
    missing = [b for b in source_blocks if b.lower() not in base_blocks]
    if missing:
        base = f"{base} {' '.join(missing)}".strip()
    return base


# ------------------------------------------------------------
# Convierte parámetros numéricos opcionales de Sonarr en enteros
# seguros para filtros de temporada, episodio o identificadores.
# ------------------------------------------------------------
def _safe_int(value: Optional[str]) -> Optional[int]:
    if value in [None, "", "null", "None"]:
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


# ------------------------------------------------------------
# Decide la consulta que Kitsunarr usará cuando Sonarr realiza una
# búsqueda Torznab, incluyendo el uso de TVDB local si Sonarr manda
# un identificador sin texto de búsqueda.
# ------------------------------------------------------------
def _build_sonarr_query(
    session: Session,
    action_type: str,
    raw_q: str,
    tvdb_id: Optional[str],
    season: Optional[int],
    episode: Optional[int],
) -> tuple[str, Optional[str], str]:
    q = (raw_q or "").strip()
    if q:
        return q, None, "q"

    if action_type in ["tvsearch", "tv-search"] and tvdb_id:
        tvdb_entry = session.exec(
            select(TVDBCache).where(TVDBCache.tvdb_id == str(tvdb_id).strip())
        ).first()
        if tvdb_entry:
            candidate_title = (tvdb_entry.series_name_es or tvdb_entry.series_name_original or "").strip()
            if candidate_title:
                return candidate_title, None, "tvdb-cache-name"

    return "", None, "empty-q"


# ------------------------------------------------------------
# Comprueba que un resultado candidato encaja con los filtros de
# temporada o episodio que Sonarr ha enviado en la búsqueda.
# ------------------------------------------------------------
def _matches_tvsearch_constraints(
    item: dict,
    requested_season: Optional[int],
    requested_episode: Optional[int],
) -> bool:
    if requested_season is None and requested_episode is None:
        return True

    title = item.get("title") or ""
    seasons = _extract_seasons(title)
    episodes = _extract_episodes(title)

    if not seasons and item.get("parsed_season") is not None:
        try:
            seasons = [int(item.get("parsed_season"))]
        except Exception:
            seasons = []

    if requested_season is not None and seasons and requested_season not in seasons:
        return False

    if requested_episode is not None and episodes and requested_episode not in episodes:
        return False

    return True


# ------------------------------------------------------------
# Normaliza datetime, ISO o RFC 2822 a UTC sin zona para persistir en SQLite.
# Recibe cualquier valor y devuelve datetime UTC-naive o None si no se interpreta;
# no hace I/O ni muta estado, captura errores de formato/rango y depende de los
# parsers estándar ISO/RFC para comparar scraper aware con filas SQLite naive.
# ------------------------------------------------------------
def _as_utc_naive_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw_value = str(value).strip()
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(raw_value)
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return None


# ------------------------------------------------------------
# Formatea una fecha de la base de datos como RFC 2822 para RSS. Si el dato
# remoto no existe o es inválido devuelve None y Torznab omite pubDate, sin
# fabricar la hora actual. Recibe cualquier valor, no tiene efectos ni propaga
# errores de formato y depende de _as_utc_naive_datetime/format_datetime.
# ------------------------------------------------------------
def _format_torznab_date(value) -> Optional[str]:
    parsed = _as_utc_naive_datetime(value)
    if parsed is None:
        return None
    return format_datetime(parsed.replace(tzinfo=timezone.utc), usegmt=True)


# ------------------------------------------------------------
# Elimina de cualquier escalar los code points que XML 1.0 no permite. Devuelve
# texto seguro para serializar, no modifica estado ni lanza por valores None y es
# la dependencia común de _xml_attr/_xml_cdata antes del escape contextual.
# ------------------------------------------------------------
def _filter_xml10_chars(value) -> str:
    text = str(value if value is not None else "")
    return "".join(
        char
        for char in text
        if (
            char in "\t\n\r"
            or 0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
    )


# ------------------------------------------------------------
# Escapa un escalar para atributos XML generados manualmente por Torznab y
# devuelve siempre str. No tiene efectos secundarios ni propaga errores por
# None; primero filtra XML 1.0 mediante _filter_xml10_chars y después usa escape.
# ------------------------------------------------------------
def _xml_attr(value) -> str:
    return escape(_filter_xml10_chars(value), quote=True)


# ------------------------------------------------------------
# Protege un escalar para contenido CDATA y devuelve siempre str. No tiene
# efectos ni errores relevantes: filtra XML 1.0 con _filter_xml10_chars y divide
# el cierre reservado para que el texto remoto no pueda terminar la sección.
# ------------------------------------------------------------
def _xml_cdata(value) -> str:
    return _filter_xml10_chars(value).replace("]]>", "]]]]><![CDATA[>")


# ------------------------------------------------------------
# Deserializa el snapshot tracker-específico de una ficha para exponer atributos
# Torznab útiles. Acepta dict o JSON y devuelve un objeto vacío ante corrupción;
# no hace I/O ni muta la entrada, captura errores JSON y depende de json.loads.
# ------------------------------------------------------------
def _parse_tracker_metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

# ------------------------------------------------------------
# Construye y devuelve el RSS Torznab para una lista de snapshots y su query. No
# modifica DB; registra cada resultado omitido por tamaño inválido, escapa XML,
# publica bytes exactos, freeleech y componentes nullable del swarm tracker. Usa
# los helpers de fechas/metadata/XML y tolera opcionales corruptos omitiéndolos.
# ------------------------------------------------------------
def generate_torznab_xml(torrents: list, query: str) -> str:
    xml_items = ""
    for t in torrents:
        try:
            size_bytes = int(t.get("size_bytes"))
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes <= 1:
            logger.warning(
                "⚠️ [TORZNAB] Resultado omitido por tamaño desconocido o inválido: "
                f"guid={t.get('guid')}"
            )
            continue

        pub_date = _format_torznab_date(t.get("published_at") or t.get("publish_date"))
        pub_date_xml = f"\n            <pubDate>{_xml_attr(pub_date)}</pubDate>" if pub_date else ""

        freeleech_until = _as_utc_naive_datetime(t.get("freeleech_until"))
        tracker_freeleech = t.get("tracker_freeleech")
        now_utc = _utcnow_naive()
        if tracker_freeleech is None:
            is_freeleech = bool(freeleech_until and now_utc < freeleech_until)
        else:
            is_freeleech = bool(tracker_freeleech) and bool(
                freeleech_until is None or now_utc < freeleech_until
            )
        dl_factor = 0 if is_freeleech else 1
            
        extra_attrs = ""
        tvdb_id = t.get('tvdb_id')
        if tvdb_id and str(tvdb_id).lower() not in ["null", "none", ""]:
            extra_attrs += f'\n            <torznab:attr name="tvdbid" value="{_xml_attr(tvdb_id)}" />'

        if is_freeleech:
            extra_attrs += '\n            <torznab:attr name="freeleech" value="1" />'

        info_hash = t.get('info_hash')
        if info_hash:
            extra_attrs += f'\n            <torznab:attr name="infohash" value="{_xml_attr(info_hash)}" />'
            
        seasons = _extract_seasons(t.get('title', ''))
        if not seasons and t.get('parsed_season') is not None:
            try:
                seasons = [int(t.get('parsed_season'))]
            except Exception:
                seasons = []
        for s in seasons:
            extra_attrs += f'\n            <torznab:attr name="season" value="{_xml_attr(s)}" />'

        for ep in _extract_episodes(t.get('title', '')):
            extra_attrs += f'\n            <torznab:attr name="episode" value="{_xml_attr(ep)}" />'

        tags = t.get('tags')
        if tags:
            try:
                parsed_tags = json.loads(tags) if isinstance(tags, str) else tags
                if isinstance(parsed_tags, list):
                    for tag in parsed_tags[:12]:
                        extra_attrs += f'\n            <torznab:attr name="tag" value="{_xml_attr(tag)}" />'
            except Exception:
                pass

        metadata = _parse_tracker_metadata(t.get("tracker_metadata"))
        stats = metadata.get("stats") if isinstance(metadata.get("stats"), dict) else metadata
        for source_key, torznab_name in (("file_count", "files"), ("grabs", "grabs"), ("uploader", "poster")):
            metadata_value = stats.get(source_key) if isinstance(stats, dict) else None
            if metadata_value not in (None, ""):
                extra_attrs += (
                    f'\n            <torznab:attr name="{torznab_name}" '
                    f'value="{_xml_attr(metadata_value)}" />'
                )

        tracker_seeders = t.get("tracker_seeders", t.get("seeders"))
        tracker_leechers = t.get("tracker_leechers", t.get("leechers"))
        normalized_seeders = None
        normalized_leechers = None
        if tracker_seeders is not None:
            try:
                normalized_seeders = max(0, int(tracker_seeders))
                extra_attrs += f'\n            <torznab:attr name="seeders" value="{normalized_seeders}" />'
            except (TypeError, ValueError):
                pass
        if tracker_leechers is not None:
            try:
                normalized_leechers = max(0, int(tracker_leechers))
                extra_attrs += f'\n            <torznab:attr name="leechers" value="{normalized_leechers}" />'
            except (TypeError, ValueError):
                pass
        if normalized_seeders is not None and normalized_leechers is not None:
            extra_attrs += (
                f'\n            <torznab:attr name="peers" '
                f'value="{normalized_seeders + normalized_leechers}" />'
            )

        download_url = t.get("download_url") or ""
        
        xml_items += f"""
        <item>
            <title><![CDATA[{_xml_cdata(t.get('title'))}]]></title>
            <guid isPermaLink="false">{_xml_attr(t.get('guid'))}</guid>
            <link><![CDATA[{_xml_cdata(download_url)}]]></link>{pub_date_xml}
            <description><![CDATA[{_xml_cdata(t.get('description') or 'Torrent')}]]></description>
            <enclosure url="{_xml_attr(download_url)}" length="{size_bytes}" type="application/x-bittorrent" />
            <torznab:attr name="category" value="5070" />
            <torznab:attr name="size" value="{size_bytes}" />
            <torznab:attr name="downloadvolumefactor" value="{dl_factor}" />
            <torznab:attr name="uploadvolumefactor" value="1" />{extra_attrs}
        </item>"""
        
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
        <channel>
            <title>Kitsunarr Multi-Indexer</title>
            <description>Resultados para '{_xml_attr(query)}'</description>
            <language>es-es</language>
            {xml_items}
        </channel>
    </rss>
    """


# ------------------------------------------------------------
# Inserta o actualiza un resultado tracker y lo enriquece con las decisiones
# locales antes de devolverlo a Sonarr. Recibe el payload mutable, identidad del
# indexador y datos para la URL; devuelve flags (creado, actualizado), abre una
# Session breve, confirma solo cambios y la cierra antes de volver al caller. La
# función puede propagar errores SQL/validación para que el endpoint los aísle
# por indexador; depende de TorrentCache, normalizadores y merge de metadata.
# ------------------------------------------------------------
def _upsert_and_enrich_torznab_result(
    result: dict,
    indexer_identifier: str,
    indexer_name: str,
    base_url: str,
    system_api_key: str,
) -> tuple[bool, bool]:
    guid = result.get("guid")
    created = False
    updated = False

    with Session(engine) as session:
        db_t = session.exec(
            select(TorrentCache).where(TorrentCache.guid == guid)
        ).first()

        title_for_log = (
            result.get("original_title") or result.get("title") or f"Torrent {guid}"
        ).strip()
        logger.info(
            f"📦 [CACHE] [SONARR] {'HIT' if db_t else 'MISS'} | "
            f"ID={guid} | Título='{title_for_log}'"
        )

        if not db_t:
            incoming_size = result.get("size_bytes")
            try:
                incoming_size = int(incoming_size) if incoming_size is not None else None
                if incoming_size is not None and incoming_size <= 1:
                    incoming_size = None
            except (TypeError, ValueError):
                incoming_size = None

            incoming_seeders = result.get("tracker_seeders", result.get("seeders"))
            incoming_leechers = result.get("tracker_leechers", result.get("leechers"))
            try:
                incoming_seeders = int(incoming_seeders) if incoming_seeders is not None else None
            except (TypeError, ValueError):
                incoming_seeders = None
            try:
                incoming_leechers = int(incoming_leechers) if incoming_leechers is not None else None
            except (TypeError, ValueError):
                incoming_leechers = None

            db_t = TorrentCache(
                guid=guid,
                source_guid=result.get("source_guid"),
                tracker_title=(
                    result.get("tracker_title")
                    or result.get("original_title")
                    or result.get("title")
                ),
                original_title=(
                    result.get("original_title") or result.get("title") or f"Torrent {guid}"
                ),
                enriched_title=(
                    result.get("title") or result.get("original_title") or f"Torrent {guid}"
                ),
                description=result.get("description"),
                poster_url=result.get("poster_url"),
                forum_url=result.get("forum_url"),
                screenshots_json=result.get("screenshots_json"),
                tracker_metadata_json=result.get("tracker_metadata_json"),
                file_entries_json=result.get("file_entries_json"),
                fansub_name=result.get("fansub") or indexer_name,
                indexer=indexer_identifier,
                download_url=f"{base_url}/api/download/{guid}_base?apikey={system_api_key}",
                published_at=_as_utc_naive_datetime(
                    result.get("published_at") or result.get("publish_date")
                ),
                last_scraped_at=(
                    _as_utc_naive_datetime(result.get("last_scraped_at"))
                    or (_utcnow_naive() if result.get("scraped_from_tracker") else None)
                ),
                scrape_revision=int(result.get("scrape_revision") or 0),
                tracker_freeleech=(
                    bool(result.get("freeleech_present"))
                    if result.get("freeleech_present") is not None
                    else None
                ),
                size_bytes=incoming_size,
                tracker_seeders=incoming_seeders,
                tracker_leechers=incoming_leechers,
                freeleech_until=(
                    _as_utc_naive_datetime(result.get("freeleech_until"))
                    if result.get("freeleech_present") is not False
                    else None
                ),
                raw_filenames=result.get("raw_filenames"),
                tags=result.get("tags"),
            )
            session.add(db_t)
            created = True
        else:
            if result.get("source_guid") and db_t.source_guid != result.get("source_guid"):
                db_t.source_guid = result.get("source_guid")
                updated = True
            if (
                result.get("tracker_title") is not None
                and db_t.tracker_title != result.get("tracker_title")
            ):
                db_t.tracker_title = result.get("tracker_title")
                updated = True
            if result.get("poster_url") and db_t.poster_url != result.get("poster_url"):
                db_t.poster_url = result.get("poster_url")
                updated = True
            if result.get("forum_url") and db_t.forum_url != result.get("forum_url"):
                db_t.forum_url = result.get("forum_url")
                updated = True
            if result.get("description") is not None and db_t.description != result.get("description"):
                db_t.description = result.get("description")
                updated = True
            if (
                result.get("raw_filenames") is not None
                and db_t.raw_filenames != result.get("raw_filenames")
            ):
                db_t.raw_filenames = result.get("raw_filenames")
                updated = True
            if (
                result.get("file_entries_json") is not None
                and db_t.file_entries_json != result.get("file_entries_json")
            ):
                db_t.file_entries_json = result.get("file_entries_json")
                updated = True
            if (
                result.get("screenshots_json") is not None
                and db_t.screenshots_json != result.get("screenshots_json")
            ):
                db_t.screenshots_json = result.get("screenshots_json")
                updated = True

            incoming_tracker_metadata = result.get("tracker_metadata")
            if isinstance(incoming_tracker_metadata, dict):
                merged_tracker_metadata = merge_tracker_metadata_json(
                    db_t.tracker_metadata_json,
                    incoming_tracker_metadata,
                )
                if db_t.tracker_metadata_json != merged_tracker_metadata:
                    db_t.tracker_metadata_json = merged_tracker_metadata
                    updated = True

            if result.get("tags") is not None and db_t.tags != result.get("tags"):
                db_t.tags = result.get("tags")
                updated = True

            incoming_published_at = _as_utc_naive_datetime(
                result.get("published_at") or result.get("publish_date")
            )
            if incoming_published_at is not None and db_t.published_at != incoming_published_at:
                db_t.published_at = incoming_published_at
                updated = True

            incoming_freeleech_until = _as_utc_naive_datetime(result.get("freeleech_until"))
            if (
                "freeleech_until" in result
                and db_t.freeleech_until != incoming_freeleech_until
            ):
                db_t.freeleech_until = incoming_freeleech_until
                updated = True
            if "freeleech_present" in result and result.get("freeleech_present") is not None:
                incoming_freeleech = bool(result.get("freeleech_present"))
                if db_t.tracker_freeleech != incoming_freeleech:
                    db_t.tracker_freeleech = incoming_freeleech
                    updated = True
                if not incoming_freeleech and db_t.freeleech_until is not None:
                    db_t.freeleech_until = None
                    updated = True

            incoming_size = result.get("size_bytes")
            try:
                incoming_size = int(incoming_size) if incoming_size is not None else None
            except (TypeError, ValueError):
                incoming_size = None
            if incoming_size and incoming_size > 1 and db_t.size_bytes != incoming_size:
                db_t.size_bytes = incoming_size
                updated = True

            incoming_seeders = result.get("tracker_seeders", result.get("seeders"))
            incoming_leechers = result.get("tracker_leechers", result.get("leechers"))
            try:
                incoming_seeders = int(incoming_seeders) if incoming_seeders is not None else None
            except (TypeError, ValueError):
                incoming_seeders = None
            try:
                incoming_leechers = int(incoming_leechers) if incoming_leechers is not None else None
            except (TypeError, ValueError):
                incoming_leechers = None
            if incoming_seeders is not None and db_t.tracker_seeders != incoming_seeders:
                db_t.tracker_seeders = incoming_seeders
                updated = True
            if incoming_leechers is not None and db_t.tracker_leechers != incoming_leechers:
                db_t.tracker_leechers = incoming_leechers
                updated = True

            incoming_last_scraped_at = _as_utc_naive_datetime(result.get("last_scraped_at"))
            if incoming_last_scraped_at is None and result.get("scraped_from_tracker"):
                incoming_last_scraped_at = _utcnow_naive()
            if (
                incoming_last_scraped_at is not None
                and db_t.last_scraped_at != incoming_last_scraped_at
            ):
                db_t.last_scraped_at = incoming_last_scraped_at
                updated = True

            incoming_revision = int(result.get("scrape_revision") or 0)
            if incoming_revision > db_t.scrape_revision:
                db_t.scrape_revision = incoming_revision
                updated = True

            incoming_fansub = result.get("fansub") or indexer_name
            if incoming_fansub and db_t.fansub_name != incoming_fansub:
                db_t.fansub_name = incoming_fansub
                updated = True

            if updated:
                session.add(db_t)

        if created or updated:
            session.commit()
            session.refresh(db_t)

        if db_t.ai_status in ["Listo", "Manual"] and db_t.ai_translated_title:
            result["title"] = _compose_sonarr_title(
                db_t.ai_translated_title,
                db_t.enriched_title,
                db_t.tvdb_id,
            )
        else:
            result["title"] = _compose_sonarr_title(
                db_t.enriched_title,
                db_t.enriched_title,
                db_t.tvdb_id,
            )

        result["tvdb_id"] = db_t.tvdb_id
        result["freeleech_until"] = db_t.freeleech_until
        result["tracker_freeleech"] = db_t.tracker_freeleech
        result["parsed_season"] = db_t.parsed_season
        result["info_hash"] = db_t.info_hash
        result["tags"] = db_t.tags_override if db_t.tags_override is not None else db_t.tags
        result["description"] = (
            db_t.description_override
            if db_t.description_override is not None
            else (db_t.description or result.get("description"))
        )
        result["size_bytes"] = db_t.size_bytes
        result["published_at"] = db_t.published_at
        result["tracker_seeders"] = db_t.tracker_seeders
        result["tracker_leechers"] = db_t.tracker_leechers
        result["tracker_metadata"] = db_t.tracker_metadata_json

    return created, updated

# ------------------------------------------------------------
# Atiende caps/search/tvsearch a partir del Request y devuelve una Response XML.
# Lee configuración/query en una Session corta, cierra antes de cada await y usa
# _upsert_and_enrich_torznab_result para una transacción breve por resultado. Sus
# efectos son red, commits, logs y wake_worker; aísla fallos por indexador, pero
# deja que errores de la lectura inicial se propaguen al handler global.
# ------------------------------------------------------------
@router.get("")
async def torznab_endpoint(request: Request):
    params = request.query_params
    action_type = (params.get("t") or "").lower().strip()
    query = params.get("q", "")
    tvdb_id = params.get("tvdbid") or params.get("tvdbId") or params.get("tvdb")
    season = _safe_int(params.get("season"))
    episode = _safe_int(params.get("ep") or params.get("episode"))
    base_url = str(request.base_url).rstrip("/")

    if action_type == "caps":
        logger.info("🤝 [SYSTEM] Sonarr está comprobando las capacidades Torznab.")
        caps_xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><caps><server version=\"1.0.0\" title=\"Kitsunarr\" /><limits max=\"100\" default=\"50\" /><retention days=\"500\" /><registration available=\"yes\" open=\"yes\" /><searching><search available=\"yes\" supportedParams=\"q\" /><tv-search available=\"yes\" supportedParams=\"q,season,ep\" /></searching><categories><category id=\"5000\" name=\"TV\"><subcat id=\"5070\" name=\"Anime\" /></category></categories></caps>"
        return Response(content=caps_xml, media_type="application/xml")

    logger.info(f"📡 [SEARCH] Búsqueda iniciada por Sonarr: '{query}'")

    if is_worker_paused():
        logger.warning(
            "⏸️ [SYSTEM] Torznab responde sin resultados: "
            "importación o mantenimiento en curso."
        )
        torznab_xml = generate_torznab_xml([], query)
        return Response(content=torznab_xml, media_type="application/xml")

    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        system_api_key = sys_config.api_key if sys_config else ""
        active_indexer_rows = session.exec(
            select(IndexerConfig).where(
                or_(
                    IndexerConfig.is_enabled == True,
                    IndexerConfig.is_enabled == None,
                )
            )
        ).all()
        active_indexers = [
            {
                "identifier": row.identifier,
                "name": row.name,
                "cookie_string": row.cookie_string,
            }
            for row in active_indexer_rows
        ]
        effective_query, fallback_query, _query_source = _build_sonarr_query(
            session=session,
            action_type=action_type,
            raw_q=query,
            tvdb_id=tvdb_id,
            season=season,
            episode=episode,
        )

    if not active_indexers:
        logger.error("❌ [SYSTEM] Búsqueda rechazada: no hay indexadores habilitados.")
        return Response(
            content=(
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<error description='No hay indexadores habilitados.'/>"
            ),
            media_type="application/xml",
            status_code=401,
        )

    if action_type in ["tvsearch", "tv-search"] and not effective_query:
        logger.info(
            "ℹ️ [SYSTEM] TV Search sin `q` en la petición de Sonarr. "
            "Se raspara solo la primera pagina para validar el indexador."
        )

    all_results = []
    seen_guids = set()
    cache_inserts = 0
    cache_updates = 0

    for idx_conf in active_indexers:
        indexer = indexer_manager.get_indexer(idx_conf["identifier"])
        if not indexer:
            continue

        cookie = (
            decrypt_secret(idx_conf["cookie_string"])
            if idx_conf["cookie_string"]
            else ""
        )

        try:
            uses_deadline = hasattr(indexer, "search_with_deadline")
            logger.info(
                f"⏳ [TORZNAB] Indexador '{idx_conf['name']}' ejecuta búsqueda "
                f"con modo_deadline={'sí' if uses_deadline else 'no'}."
            )
            if uses_deadline:
                results = await indexer.search_with_deadline(effective_query, cookie)
            else:
                results = await indexer.search(effective_query, cookie)

            if not results and fallback_query and fallback_query != effective_query:
                logger.info(
                    "🔁 [INDEXER] Sin resultados con query principal; "
                    f"fallback a '{fallback_query}'."
                )
                if uses_deadline:
                    results = await indexer.search_with_deadline(fallback_query, cookie)
                else:
                    results = await indexer.search(fallback_query, cookie)

            logger.info(
                f"📊 [TORZNAB] Indexador '{idx_conf['name']}' devolvió "
                f"{len(results)} resultados para query='{effective_query or query}'."
            )

            for result in results:
                guid = result.get("guid")
                if not guid or guid in seen_guids:
                    continue

                created, updated = _upsert_and_enrich_torznab_result(
                    result=result,
                    indexer_identifier=idx_conf["identifier"],
                    indexer_name=idx_conf["name"],
                    base_url=base_url,
                    system_api_key=system_api_key,
                )
                cache_inserts += int(created)
                cache_updates += int(updated)

                if not _matches_tvsearch_constraints(result, season, episode):
                    continue

                result["download_url"] = (
                    f"{base_url}/api/download/{guid}_base?apikey={system_api_key}"
                )
                seen_guids.add(guid)
                all_results.append(result)

        except Exception as exc:
            logger.error(
                f"❌ [INDEXER] Error buscando en el indexador "
                f"'{idx_conf['name']}': {exc}"
            )

    if cache_inserts or cache_updates:
        logger.info(
            "💾 [SYSTEM] Caché de torrents actualizada desde Torznab: "
            f"nuevas={cache_inserts}, actualizadas={cache_updates}."
        )

    wake_worker(f"búsqueda Sonarr '{effective_query or query or 'vacía'}'")

    torznab_xml = generate_torznab_xml(all_results, effective_query)
    return Response(content=torznab_xml, media_type="application/xml")
