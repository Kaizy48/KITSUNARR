import asyncio
import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import tvdb_v4_official
from sqlmodel import Session, delete, select, or_, and_

from core.database.engine import engine
from core.app.logger import logger
from core.database.models import SystemConfig, TorrentCache, TorrentTVDBCandidates, TVDBCache, TVDBEpisodes
from core.app.encrypt import decrypt_secret

_tvdb_series_locks: dict[str, asyncio.Lock] = {}
_tvdb_episodes_locks: dict[str, asyncio.Lock] = {}

# ------------------------------------------------------------
# Limpia un título de torrent para convertirlo en una consulta útil
# contra TheTVDB, retirando fansubs, temporada y metadatos técnicos.
# ------------------------------------------------------------
def clean_for_tvdb(title: str) -> str:
    clean = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    clean = re.sub(r"(?i)\b(S\d{1,2}|Temporada\s*\d{1,2}|Season\s*\d{1,2})\b", "", clean)
    clean = re.sub(r"\s+", " ", clean)
    clean = re.sub(r"^\s*-\s*|\s*-\s*$", "", clean)
    return clean.strip()


# ------------------------------------------------------------
# Normaliza texto de títulos y alias para comparar candidatos TVDB
# ignorando mayúsculas, acentos, símbolos y espaciado.
# ------------------------------------------------------------
def _normalize_match_text(text: str) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", str(text))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"\[.*?\]|\(.*?\)", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


# ------------------------------------------------------------
# Reúne todos los nombres y alias relevantes de un candidato TVDB
# para que Kitsunarr pueda compararlos con el título del torrent.
# ------------------------------------------------------------
def _iter_candidate_names(candidate: dict) -> list[str]:
    raw_names = []
    if candidate.get("name"):
        raw_names.append(str(candidate.get("name")))
    if candidate.get("original_name"):
        raw_names.append(str(candidate.get("original_name")))

    aliases = candidate.get("aliases") or []
    for alias in aliases:
        if isinstance(alias, dict):
            alias_name = alias.get("name")
        else:
            alias_name = alias
        if alias_name:
            raw_names.append(str(alias_name))

    dedup = []
    seen = set()
    for name in raw_names:
        key = _normalize_match_text(name)
        if key and key not in seen:
            seen.add(key)
            dedup.append(name)
    return dedup


# ------------------------------------------------------------
# Detecta si entre los candidatos TVDB hay una coincidencia evidente
# con el título limpio para vincular automáticamente la ficha.
# ------------------------------------------------------------
def _find_obvious_candidate(clean_title: str, candidates: list[dict]) -> tuple[str | None, float]:
    q = _normalize_match_text(clean_title)
    if not q or not candidates:
        return None, 0.0

    scored = []
    for cand in candidates:
        cand_id = cand.get("tvdb_id")
        if not cand_id:
            continue

        best = 0.0
        for name in _iter_candidate_names(cand):
            n = _normalize_match_text(name)
            if not n:
                continue
            if n == q:
                return str(cand_id), 1.0

            ratio = SequenceMatcher(None, q, n).ratio()
            if q in n or n in q:
                ratio = max(ratio, 0.93)
            best = max(best, ratio)

        scored.append((best, str(cand_id)))

    if not scored:
        return None, 0.0

    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_id = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    if top_score >= 0.97:
        return top_id, top_score
    if top_score >= 0.94 and (top_score - second_score) >= 0.08:
        return top_id, top_score
    return None, top_score


# ------------------------------------------------------------
# Interpreta la fecha de actualización guardada en la biblioteca
# local de TVDB para decidir si una ficha necesita refresco.
# ------------------------------------------------------------
def _parse_last_updated(value) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        dt_value = value
    elif isinstance(value, str):
        try:
            dt_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    else:
        return None

    if dt_value.tzinfo is not None:
        dt_value = dt_value.astimezone(timezone.utc).replace(tzinfo=None)
    return dt_value

# ------------------------------------------------------------
# Extrae nombres y sinopsis en varios idiomas desde una respuesta de
# búsqueda de TheTVDB y decide los mejores valores para Kitsunarr.
# ------------------------------------------------------------
def _extract_translation_bundle(r_dict: dict) -> dict:
    name_eng = None
    name_spa = None
    name_jpn = None
    overview_eng = None
    overview_spa = None
    original_name = r_dict.get("name", "Desconocido")

    translations = r_dict.get("translations", {})
    overviews = r_dict.get("overviews", {})

    if isinstance(translations, dict):
        name_eng = translations.get("eng")
        name_spa = translations.get("spa")
        name_jpn = translations.get("jpn")
        for t in translations.get("nameTranslations", []):
            if isinstance(t, dict):
                if t.get("language") == "eng": name_eng = t.get("name")
                if t.get("language") == "spa": name_spa = t.get("name")
            if t.get("language") == "jpn": name_jpn = t.get("name")

        for t in translations.get("overviewTranslations", []):
            if isinstance(t, dict):
                if t.get("language") == "eng": overview_eng = t.get("overview")
                if t.get("language") == "spa": overview_spa = t.get("overview")

    if isinstance(overviews, dict):
        if not overview_eng: overview_eng = overviews.get("eng")
        if not overview_spa: overview_spa = overviews.get("spa")

    if not name_eng and not name_spa and not all(ord(c) < 128 for c in original_name):
        for alias in r_dict.get("aliases", []):
            alias_name = alias if isinstance(alias, str) else alias.get("name", "")
            if alias_name and all(ord(c) < 128 for c in alias_name):
                name_eng = alias_name
                break

    best_name = name_eng or name_spa or original_name
    best_overview = overview_spa or overview_eng or r_dict.get("overview", "Sin sinopsis")

    return {
        "name_eng": name_eng,
        "name_spa": name_spa,
        "name_jpn": name_jpn,
        "overview_eng": overview_eng,
        "overview_spa": overview_spa,
        "original_name": original_name,
        "best_name": best_name,
        "best_overview": best_overview,
    }

# ------------------------------------------------------------
# Devuelve el mejor nombre, sinopsis y nombre original de un resultado
# TVDB usando el paquete de traducciones disponible.
# ------------------------------------------------------------
def _extract_best_name(r_dict: dict) -> tuple:
    bundle = _extract_translation_bundle(r_dict)
    return bundle["best_name"], bundle["best_overview"], bundle["original_name"]


# ------------------------------------------------------------
# Construye el cliente oficial de TheTVDB usando la API key guardada
# y el PIN opcional configurado para Kitsunarr.
# ------------------------------------------------------------
def _build_tvdb_client(tvdb_api_key: str):
    tvdb_pin = (os.getenv("KITSUNARR_TVDB_PIN", "") or "").strip()
    return tvdb_v4_official.TVDB(tvdb_api_key, pin=tvdb_pin) if tvdb_pin else tvdb_v4_official.TVDB(tvdb_api_key)


# ------------------------------------------------------------
# Ejecuta llamadas a TheTVDB con reintentos para suavizar errores
# temporales de red o del servicio oficial.
# ------------------------------------------------------------
async def _tvdb_call_with_retry(callable_obj, *args, retries: int = 4, base_delay: float = 0.8, **kwargs):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncio.to_thread(callable_obj, *args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt >= retries:
                break
            await asyncio.sleep(base_delay * attempt)
    if last_exc:
        raise last_exc
    return None

# ------------------------------------------------------------
# Busca series en TheTVDB desde Kitsunarr. En modo manual devuelve
# resultados al usuario y en modo automático guarda candidatos básicos
# para que IA y TVDB puedan continuar el flujo.
# ------------------------------------------------------------
async def search_tvdb(query: str, is_interactive: bool = True, limit: int = 8) -> list | None:
    if not query: return []

    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        
        if not config or not config.tvdb_api_key:
            if is_interactive: raise Exception("La API Key de TheTVDB no está configurada.")
            return []
            
        if not is_interactive and not config.tvdb_is_enabled:
            logger.info("⏸️ [TVDB] Búsqueda automática omitida: TheTVDB está en OFF en Configuración.")
            return []
            
        tvdb_api_key = decrypt_secret(config.tvdb_api_key)

    if is_interactive:
        logger.info(f"🔎 [TVDB] Búsqueda manual iniciada: '{query}'")
        
    try:
        tvdb = _build_tvdb_client(tvdb_api_key)
        results = await _tvdb_call_with_retry(tvdb.search, query, type="series", limit=limit)
        if not results: return []

        formatted_results = []
        for r in results:
            safe_id = str(r.get("tvdb_id"))
            bundle = _extract_translation_bundle(r)
            best_name = bundle["best_name"]
            best_overview = bundle["best_overview"]
            original_name = bundle["original_name"]

            formatted_results.append({
                "tvdb_id": safe_id,
                "name": best_name,
                "original_name": original_name,
                "aliases": r.get("aliases", []),
                "year": r.get("year", "Desconocido"),
                "image_url": r.get("image_url"),
                "overview": best_overview,
                "status": r.get("status", "Desconocido")
            })

        if is_interactive:
            logger.info(f"✅ [TVDB] Búsqueda completada: {len(formatted_results)} resultados para '{query}'.")
            return formatted_results
        else:
            with Session(engine) as session:
                for idx, cand in enumerate(formatted_results):
                    src = results[idx] if idx < len(results) else {}
                    cand_bundle = _extract_translation_bundle(src) if isinstance(src, dict) else {}
                    existing_tvdb = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == cand["tvdb_id"])).first()
                    if not existing_tvdb:
                        name_spa = cand_bundle.get("name_spa")
                        name_eng = cand_bundle.get("name_eng")
                        name_jpn = cand_bundle.get("name_jpn")
                        new_tvdb = TVDBCache(
                            tvdb_id=cand["tvdb_id"],
                            series_name_es=name_eng or cand["name"],
                            series_name_original=name_jpn or cand["original_name"] or cand["name"],
                            series_name_jp=name_jpn,
                            overview_basic=cand_bundle.get("overview_spa") or cand["overview"],
                            overview_es=cand_bundle.get("overview_spa"),
                            overview_original=cand_bundle.get("overview_eng"),
                            poster_path=cand["image_url"],
                            first_aired=cand["year"],
                            status=cand["status"],
                            is_full_record=False,
                            last_updated=datetime.utcnow().isoformat()
                        )
                        session.add(new_tvdb)
                session.commit()
            return formatted_results

    except Exception as e:
        logger.error(f"❌ [TVDB] Error en search_tvdb para '{query}': {e}")
        if is_interactive: raise
        return None

# ------------------------------------------------------------
# Descarga o actualiza la ficha maestra de una serie TVDB con nombres,
# alias, sinopsis, póster, temporadas y lanza la descarga de episodios.
# ------------------------------------------------------------
async def fetch_full_tvdb_series(tvdb_id: str, await_episodes: bool = False) -> bool:
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key: return False
        tvdb_api_key = decrypt_secret(config.tvdb_api_key)
        
    lock = _tvdb_series_locks.setdefault(str(tvdb_id), asyncio.Lock())
    async with lock:
        try:
            logger.info(f"🔄 [TVDB] Actualizando ficha maestra de la serie {tvdb_id}.")
            safe_id = int(tvdb_id)
            tvdb = _build_tvdb_client(tvdb_api_key)
            
            series_data = await _tvdb_call_with_retry(tvdb.get_series_extended, safe_id, meta="translations")
            if not series_data: return False
                
            try: trans_eng = await _tvdb_call_with_retry(tvdb.get_series_translation, safe_id, "eng")
            except: trans_eng = {}
            try: trans_spa = await _tvdb_call_with_retry(tvdb.get_series_translation, safe_id, "spa")
            except: trans_spa = {}
            try: trans_jpn = await _tvdb_call_with_retry(tvdb.get_series_translation, safe_id, "jpn")
            except: trans_jpn = {}
        
            original_name = series_data.get("name", "Desconocido")

            name_en = trans_eng.get("name") or original_name
            name_es = trans_spa.get("name")
            name_jp = trans_jpn.get("name")
            
            raw_aliases = series_data.get("aliases", [])
            aliases_list = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
            
            seasons_dict = {}
            for s in series_data.get("seasons", []):
                if s.get("type", {}).get("id") == 1:
                    s_num = str(s.get("number"))
                    if s_num != "0": seasons_dict[s_num] = {"id": s.get("id"), "episodes": 0}
            
            with Session(engine) as session:
                db_series = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == str(tvdb_id))).first()
                if not db_series:
                    db_series = TVDBCache(
                        tvdb_id=str(tvdb_id),
                        series_name_es=name_es or name_en or original_name,
                        series_name_original=name_en or original_name,
                        series_name_jp=name_jp,
                        last_updated=datetime.utcnow().isoformat(),
                    )
                    session.add(db_series)
                
                db_series.series_name_es = name_en or original_name
                db_series.series_name_original = name_jp or original_name
                db_series.series_name_jp = name_jp
                db_series.aliases = json.dumps(aliases_list, ensure_ascii=False) if aliases_list else None
                
                db_series.overview_es = trans_spa.get("overview")
                db_series.overview_original = trans_eng.get("overview") or series_data.get("overview")
                db_series.overview_basic = db_series.overview_es or db_series.overview_original or series_data.get("overview")
                
                db_series.poster_path = series_data.get("image")
                db_series.banner_path = series_data.get("banner")
                db_series.status = series_data.get("status", {}).get("name")
                db_series.first_aired = series_data.get("firstAired")
                db_series.seasons_data = json.dumps(seasons_dict, ensure_ascii=False)
                
                db_series.is_full_record = True
                db_series.last_updated = datetime.utcnow().isoformat()
                session.commit()

                if await_episodes:
                    await fetch_tvdb_episodes(str(tvdb_id))
                else:
                    asyncio.create_task(fetch_tvdb_episodes(str(tvdb_id)))
                logger.info(f"✅ [TVDB] Ficha maestra actualizada para la serie {tvdb_id}.")
                return True
            
        except Exception as e:
            logger.error(f"❌ [TVDB] No se pudo actualizar la ficha maestra de la serie {tvdb_id}: {e}")
            return False

# ------------------------------------------------------------
# Descarga los episodios de una serie TVDB y los guarda en la
# biblioteca local para mostrar temporadas y nombres de episodio.
# ------------------------------------------------------------
async def fetch_tvdb_episodes(tvdb_id: str):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key: return
        tvdb_api_key = decrypt_secret(config.tvdb_api_key)
        
    lock = _tvdb_episodes_locks.setdefault(str(tvdb_id), asyncio.Lock())
    async with lock:
        logger.info(f"📥 [TVDB] Descargando episodios de la serie {tvdb_id}...")
        try:
            tvdb = _build_tvdb_client(tvdb_api_key)
            page = 0
            has_more = True
            total_episodes = 0
            safe_tvdb_id = int(tvdb_id)
            
            with Session(engine) as session:
                session.exec(delete(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == str(tvdb_id)))
                session.commit()
            
            while has_more:
                try: episodes_eng = await _tvdb_call_with_retry(tvdb.get_series_episodes, safe_tvdb_id, page=page, season_type="default", lang="eng")
                except: episodes_eng = {}
                try: episodes_spa = await _tvdb_call_with_retry(tvdb.get_series_episodes, safe_tvdb_id, page=page, season_type="default", lang="spa")
                except: episodes_spa = {}
                try: episodes_orig = await _tvdb_call_with_retry(tvdb.get_series_episodes, safe_tvdb_id, page=page, season_type="default")
                except: episodes_orig = {}
            
                list_eng = episodes_eng.get("episodes", [])
                list_spa = episodes_spa.get("episodes", [])
                list_orig = episodes_orig.get("episodes", [])
                
                if not list_orig and not list_eng: break
                    
                dict_eng = {ep["id"]: ep for ep in list_eng}
                dict_spa = {ep["id"]: ep for ep in list_spa}
                dict_orig = {ep["id"]: ep for ep in list_orig}
                
                with Session(engine) as session:
                    for ep_id, ep_jp in dict_orig.items():
                        season_num = ep_jp.get("seasonNumber")
                        ep_num = ep_jp.get("number")
                        
                        if season_num is not None and ep_num is not None and int(season_num) > 0:
                            ep_en = dict_eng.get(ep_id, {})
                            ep_es = dict_spa.get(ep_id, {})
                            
                            raw_name = (
                                ep_es.get("name") or 
                                ep_en.get("name") or 
                                ep_jp.get("name") or 
                                f"Episodio {ep_num}"
                            )
                            
                            formatted_name = f"S{int(season_num):02d}E{int(ep_num):02d} - {raw_name}"
                            
                            existing_ep = session.exec(
                                select(TVDBEpisodes).where(TVDBEpisodes.episode_id == int(ep_id))
                            ).first()
                            if existing_ep:
                                continue

                            new_ep = TVDBEpisodes(
                                tvdb_id=str(tvdb_id),
                                episode_id=int(ep_id),
                                season_number=int(season_num),
                                episode_number=int(ep_num),
                                name_es=formatted_name,
                                name_original=ep_jp.get("name"),
                                air_date=ep_es.get("aired") or ep_jp.get("aired")
                            )
                            session.add(new_ep)
                            total_episodes += 1
                    session.commit()
                
                page += 1
                if page > 20 or len(list_orig) < 100: 
                    has_more = False
                await asyncio.sleep(0.1)

            with Session(engine) as session:
                tvdb_row = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == str(tvdb_id))).first()
                if tvdb_row:
                    tvdb_row.last_updated = datetime.utcnow().isoformat()
                    session.add(tvdb_row)
                    session.commit()
            
            logger.info(f"✅ [TVDB] Episodios actualizados para la serie {tvdb_id}: {total_episodes} guardados.")
            
        except Exception as e:
            logger.error(f"❌ [TVDB] No se pudieron actualizar los episodios de la serie {tvdb_id}: {e}")

# ------------------------------------------------------------
# Refresca en segundo plano fichas TVDB ya identificadas cuando
# faltan datos completos o llevan suficiente tiempo sin actualizarse.
# ------------------------------------------------------------
async def refresh_identified_tvdb_library(max_series_per_cycle: int = 2, stale_hours: int = 24):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key or not config.tvdb_is_enabled:
            return

        linked_ids_raw = session.exec(
            select(TorrentCache.tvdb_id).where(
                TorrentCache.tvdb_id != None,
                TorrentCache.tvdb_status == "Listo"
            )
        ).all()

        linked_ids = sorted({str(tvdb_id) for tvdb_id in linked_ids_raw if tvdb_id})
        if not linked_ids:
            return

        cached_rows = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id.in_(linked_ids))).all()
        by_id = {row.tvdb_id: row for row in cached_rows}

    cutoff = datetime.utcnow() - timedelta(days=7)
    targets: list[str] = []
    missing_or_partial = 0
    stale_found = 0

    for tvdb_id in linked_ids:
        row = by_id.get(tvdb_id)
        if not row or not row.is_full_record:
            targets.append(tvdb_id)
            missing_or_partial += 1
            continue

        parsed_last = _parse_last_updated(row.last_updated)
        if not parsed_last or parsed_last < cutoff:
            targets.append(tvdb_id)
            stale_found += 1
        else:
            logger.info(
                f"⏭️ [TVDB] Skip refresh automático TVDB={tvdb_id}: actualizada recientemente ({row.last_updated})."
            )

    if not targets:
        return

    targets = targets[:max_series_per_cycle]
    logger.info(
        f"🔄 [TVDB] Sincronización biblioteca (worker): {len(targets)} series a refrescar "
        f"(faltantes/incompletas={missing_or_partial}, desactualizadas>7d={stale_found})."
    )

    refreshed = 0
    for tvdb_id in targets:
        ok = await fetch_full_tvdb_series(tvdb_id)
        if ok:
            refreshed += 1
        await asyncio.sleep(0.4)

    logger.info(f"✅ [TVDB] Sincronización biblioteca completada: {refreshed}/{len(targets)} series actualizadas.")

# ------------------------------------------------------------
# Procesa fichas torrent pendientes de identificación TVDB. Busca
# candidatos, vincula automáticamente coincidencias claras y deja
# candidatos disponibles para revisión o para la IA.
# ------------------------------------------------------------
async def process_pending_tvdb():
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key:
            logger.warning("⚠️ [TVDB] Actualización automática en pausa: falta configurar API Key de TheTVDB.")
            return

        if not config.tvdb_is_enabled:
            logger.info("⏸️ [TVDB] Actualización automática en pausa: TheTVDB está desactivado.")
            return

        pending_torrents = session.exec(
            select(TorrentCache)
            .where(TorrentCache.tvdb_id == None)
            .where(
                or_(
                    TorrentCache.tvdb_status == "Pendiente",
                    and_(TorrentCache.tvdb_status == "No Encontrado", TorrentCache.ai_status == "Listo")
                )
            )
            .limit(1)
        ).all()
        
        if not pending_torrents: return
            
        logger.info(f"🔍 [TVDB] Procesando {len(pending_torrents)} ficha pendiente.")
        
        for torrent in pending_torrents:
            source_title = torrent.ai_translated_title or torrent.enriched_title or torrent.original_title
            clean_title = clean_for_tvdb(source_title)
            if not clean_title:
                clean_title = clean_for_tvdb(torrent.original_title)

            candidates = await search_tvdb(clean_title, is_interactive=False, limit=5)

            if candidates is None:
                torrent.tvdb_status = "Pendiente"
                logger.warning(f"⚠️ [TVDB] Error temporal consultando TVDB para '{clean_title}'. Se reintentará.")
                session.add(torrent)
                session.commit()
                await asyncio.sleep(2.0)
                continue
            
            if candidates:
                obvious_tvdb_id, obvious_score = _find_obvious_candidate(clean_title, candidates)

                for cand in candidates:
                    existing_link = session.exec(
                        select(TorrentTVDBCandidates).where(
                            TorrentTVDBCandidates.torrent_guid == torrent.guid,
                            TorrentTVDBCandidates.tvdb_id == cand["tvdb_id"]
                        )
                    ).first()
                    
                    if not existing_link:
                        new_link = TorrentTVDBCandidates(torrent_guid=torrent.guid, tvdb_id=cand["tvdb_id"])
                        session.add(new_link)

                if obvious_tvdb_id:
                    torrent.tvdb_id = obvious_tvdb_id
                    torrent.tvdb_status = "Listo"
                    if torrent.ai_status == "Listo":
                        torrent.ai_status = "Pendiente"
                    should_refresh = True
                    existing_tvdb = session.exec(
                        select(TVDBCache).where(TVDBCache.tvdb_id == obvious_tvdb_id)
                    ).first()
                    if existing_tvdb and existing_tvdb.is_full_record:
                        parsed_last = _parse_last_updated(existing_tvdb.last_updated)
                        if parsed_last and parsed_last >= (datetime.utcnow() - timedelta(days=7)):
                            should_refresh = False
                            logger.info(f"⏭️ [TVDB] Se omite actualización de la serie {obvious_tvdb_id}: ya fue actualizada en los últimos 7 días.")

                    if should_refresh:
                        logger.info(f"📥 [TVDB] Actualizando serie {obvious_tvdb_id} tras vinculación automática.")
                        asyncio.create_task(fetch_full_tvdb_series(obvious_tvdb_id))
                    logger.info(
                        f"🎯 [TVDB] Vinculación automática '{clean_title}' -> TVDB {obvious_tvdb_id} "
                        f"(match={obvious_score * 100:.1f}%)."
                    )
                else:
                    torrent.tvdb_status = "Candidatos"
                    if not torrent.tvdb_id and torrent.ai_status == "Listo":
                        torrent.ai_status = "Pendiente"
                    logger.info(f"✅ [TVDB] Se enlazaron {len(candidates)} candidatos para '{clean_title}'.")
            else:
                torrent.tvdb_status = "No Encontrado"
                logger.warning(f"⚠️ [TVDB] Sin resultados para '{clean_title}'.")
                
            session.add(torrent)
            session.commit()
            
            await asyncio.sleep(2.0)
