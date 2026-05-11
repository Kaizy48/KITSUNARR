import re
import time
import json
from datetime import datetime
from email.utils import formatdate
from fastapi import APIRouter, Request, Response
from sqlmodel import Session, select, or_
from typing import Optional

from core.database.engine import engine
from core.database.models import SystemConfig, IndexerConfig, TorrentCache, TVDBCache
from core.app.logger import logger
from core.app.background import wake_worker, is_worker_paused
from core.app.encrypt import decrypt_secret
from core.app.indexers.manager import indexer_manager

router = APIRouter(prefix="/api", tags=["Torznab"])

_TECH_BLOCK_PATTERN = re.compile(
    r"(?i)(1080p|720p|2160p|1440p|4k|8k|x264|x265|h\.?264|h\.?265|hevc|av1|"
    r"blu-?ray|bdrip|bdremux|web-?dl|webrip|hdtv|dvd|audio\s*:|subs?\s*:|"
    r"flac|aac|ac3|dts|eac3|mp3|opus|mkv|mp4|avi|softsubs|hardsubs|hdr|dv)"
)

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
# Construye el RSS Torznab que reciben Sonarr y otros clientes Arr
# con resultados, atributos técnicos, temporada, TVDB, freeleech e
# info hash cuando Kitsunarr dispone de esos datos.
# ------------------------------------------------------------
def generate_torznab_xml(torrents: list, query: str) -> str:
    xml_items = ""
    for t in torrents:
        size_bytes = t.get('size_bytes', 1) if t.get('size_bytes', 1) > 0 else 1
        pub_date = t.get('publish_date') or formatdate(time.time(), localtime=False, usegmt=True)

        is_freeleech = bool(t.get('freeleech_until') and datetime.utcnow() < t.get('freeleech_until'))
        dl_factor = 0 if is_freeleech else 1
            
        extra_attrs = ""
        tvdb_id = t.get('tvdb_id')
        if tvdb_id and str(tvdb_id).lower() not in ["null", "none", ""]:
            extra_attrs += f'\n            <torznab:attr name="tvdbid" value="{tvdb_id}" />'

        if is_freeleech:
            extra_attrs += '\n            <torznab:attr name="freeleech" value="1" />'

        info_hash = t.get('info_hash')
        if info_hash:
            extra_attrs += f'\n            <torznab:attr name="infohash" value="{info_hash}" />'
            
        seasons = _extract_seasons(t.get('title', ''))
        if not seasons and t.get('parsed_season') is not None:
            try:
                seasons = [int(t.get('parsed_season'))]
            except Exception:
                seasons = []
        for s in seasons:
            extra_attrs += f'\n            <torznab:attr name="season" value="{s}" />'

        for ep in _extract_episodes(t.get('title', '')):
            extra_attrs += f'\n            <torznab:attr name="episode" value="{ep}" />'

        tags = t.get('tags')
        if tags:
            try:
                parsed_tags = json.loads(tags) if isinstance(tags, str) else tags
                if isinstance(parsed_tags, list):
                    for tag in parsed_tags[:12]:
                        safe_tag = str(tag).replace('"', '')
                        extra_attrs += f'\n            <torznab:attr name="tag" value="{safe_tag}" />'
            except Exception:
                pass
        
        xml_items += f"""
        <item>
            <title><![CDATA[{t.get('title')}]]></title>
            <guid isPermaLink="false">{t.get('guid')}</guid>
            <link><![CDATA[{t.get('download_url')}]]></link>
            <pubDate>{pub_date}</pubDate>
            <description><![CDATA[{t.get('description', 'Torrent')}]]></description>
            <enclosure url="{t.get('download_url')}" length="{size_bytes}" type="application/x-bittorrent" />
            <torznab:attr name="category" value="5070" />
            <torznab:attr name="seeders" value="{t.get('seeders', 0)}" />
            <torznab:attr name="peers" value="{t.get('seeders', 0) + t.get('leechers', 0)}" />
            <torznab:attr name="size" value="{size_bytes}" />
            <torznab:attr name="downloadvolumefactor" value="{dl_factor}" />
            <torznab:attr name="uploadvolumefactor" value="1" />{extra_attrs}
        </item>"""
        
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
        <channel>
            <title>Kitsunarr Multi-Indexer</title>
            <description>Resultados para '{query}'</description>
            <language>es-es</language>
            {xml_items}
        </channel>
    </rss>
    """

# ------------------------------------------------------------
# Entrada Torznab de Kitsunarr para capacidades, búsquedas y
# tvsearch. Consulta los indexadores activos, sincroniza la caché
# local y devuelve a Sonarr un XML compatible.
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
        logger.warning("⏸️ [SYSTEM] Torznab responde sin resultados: importación o mantenimiento en curso.")
        torznab_xml = generate_torznab_xml([], query)
        return Response(content=torznab_xml, media_type="application/xml")

    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        system_api_key = sys_config.api_key if sys_config else ""
        pending_cache_writes = False
        
        active_indexers = session.exec(
            select(IndexerConfig).where(or_(IndexerConfig.is_enabled == True, IndexerConfig.is_enabled == None))
        ).all()
        
        if not active_indexers:
            logger.error("❌ [SYSTEM] Búsqueda rechazada: no hay indexadores habilitados.")
            return Response(content="<?xml version='1.0' encoding='UTF-8'?><error description='No hay indexadores habilitados.'/>", media_type="application/xml", status_code=401)

        effective_query, fallback_query, query_source = _build_sonarr_query(
            session=session,
            action_type=action_type,
            raw_q=query,
            tvdb_id=tvdb_id,
            season=season,
            episode=episode,
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
            indexer = indexer_manager.get_indexer(idx_conf.identifier)
            if not indexer: continue
                
            cookie = decrypt_secret(idx_conf.cookie_string) if idx_conf.cookie_string else ""
            
            try:
                results = await indexer.search(effective_query, cookie)

                if not results and fallback_query and fallback_query != effective_query:
                    logger.info(f"🔁 [INDEXER] Sin resultados con query principal; fallback a '{fallback_query}'.")
                    results = await indexer.search(fallback_query, cookie)
                
                result_guids = [str(r.get("guid")) for r in results if r.get("guid")]
                existing_by_guid = {}
                if result_guids:
                    existing_rows = session.exec(
                        select(TorrentCache).where(TorrentCache.guid.in_(result_guids))
                    ).all()
                    existing_by_guid = {row.guid: row for row in existing_rows}

                for r in results:
                    guid = r.get("guid")
                    if not guid or guid in seen_guids:
                        continue

                    db_t = existing_by_guid.get(guid)
                    title_for_log = (r.get("original_title") or r.get("title") or f"Torrent {guid}").strip()
                    logger.info(
                        f"📦 [CACHE] [SONARR] {'HIT' if db_t else 'MISS'} | ID={guid} | Título='{title_for_log}'"
                    )

                    if not db_t:
                        db_t = TorrentCache(
                            guid=guid,
                            source_guid=r.get("source_guid"),
                            original_title=r.get("original_title") or r.get("title") or f"Torrent {guid}",
                            enriched_title=r.get("title") or r.get("original_title") or f"Torrent {guid}",
                            description=r.get("description"),
                            poster_url=r.get("poster_url"),
                            fansub_name=r.get("fansub") or idx_conf.name,
                            indexer=idx_conf.identifier,
                            download_url=f"{base_url}/api/download/{guid}_base?apikey={system_api_key}",
                            pub_date=r.get("publish_date"),
                            size_bytes=r.get("size_bytes") or 0,
                            freeleech_until=r.get("freeleech_until"),
                            raw_filenames=r.get("raw_filenames"),
                            tags=r.get("tags"),
                        )
                        session.add(db_t)
                        existing_by_guid[guid] = db_t
                        pending_cache_writes = True
                        cache_inserts += 1
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
                        if r.get("raw_filenames") and not db_t.raw_filenames:
                            db_t.raw_filenames = r.get("raw_filenames")
                            updated = True
                        if r.get("tags") and not db_t.tags:
                            db_t.tags = r.get("tags")
                            updated = True
                        if r.get("publish_date") and not db_t.pub_date:
                            db_t.pub_date = r.get("publish_date")
                            updated = True
                        if r.get("freeleech_until") and db_t.freeleech_until != r.get("freeleech_until"):
                            db_t.freeleech_until = r.get("freeleech_until")
                            updated = True
                        incoming_fansub = r.get("fansub") or idx_conf.name
                        if incoming_fansub and db_t.fansub_name != incoming_fansub:
                            db_t.fansub_name = incoming_fansub
                            updated = True

                        if updated:
                            session.add(db_t)
                            pending_cache_writes = True
                            cache_updates += 1
                    
                    if db_t:
                        if db_t.ai_status in ["Listo", "Manual"] and db_t.ai_translated_title:
                            r["title"] = _compose_sonarr_title(db_t.ai_translated_title, db_t.enriched_title, db_t.tvdb_id)
                        else:
                            r["title"] = _compose_sonarr_title(db_t.enriched_title, db_t.enriched_title, db_t.tvdb_id)

                        r["tvdb_id"] = db_t.tvdb_id
                        r["freeleech_until"] = db_t.freeleech_until
                        r["parsed_season"] = db_t.parsed_season
                        r["info_hash"] = db_t.info_hash
                        r["tags"] = db_t.tags
                        r["description"] = db_t.description or r.get("description")

                    if not _matches_tvsearch_constraints(r, season, episode):
                        continue
                    
                    r["download_url"] = f"{base_url}/api/download/{guid}_base?apikey={system_api_key}"
                    seen_guids.add(guid)
                    all_results.append(r)
                    
            except Exception as e:
                logger.error(f"❌ [INDEXER] Error buscando en el indexador '{idx_conf.name}': {e}")

        if pending_cache_writes:
            session.commit()
            logger.info(
                f"💾 [SYSTEM] Caché de torrents actualizada desde Torznab: nuevas={cache_inserts}, actualizadas={cache_updates}."
            )

    wake_worker(f"búsqueda Sonarr '{effective_query or query or 'vacía'}'")

    torznab_xml = generate_torznab_xml(all_results, effective_query)
    return Response(content=torznab_xml, media_type="application/xml")
