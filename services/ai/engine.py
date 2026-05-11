import json
import re
import asyncio
from datetime import datetime, timedelta

import httpx
from sqlmodel import Session, select, delete, or_

from core.app.logger import logger
from core.database.engine import engine
from core.database.models import AIConfig, AIModel, SystemConfig, TorrentCache, TVDBCache, TorrentTVDBCandidates
from services.tvdb.tvdb_api import fetch_full_tvdb_series, fetch_tvdb_episodes 
from core.app.encrypt import decrypt_secret

_ai_sleep_until_by_key: dict[str, datetime] = {}
_ai_last_quota_log_by_key: dict[str, datetime] = {}


# ------------------------------------------------------------
# Devuelve el estado de pausa temporal de un modelo de IA para que
# Kitsunarr pueda mostrar si el worker está activo o esperando.
# ------------------------------------------------------------
def get_ai_model_backoff_state(model_key: str) -> dict:
    until = _ai_sleep_until_by_key.get((model_key or "").strip().lower())
    now = datetime.now()
    if until and now < until:
        return {
            "status": "paused",
            "until_iso": until.isoformat(),
            "remaining_seconds": int((until - now).total_seconds())
        }
    return {"status": "active", "until_iso": None, "remaining_seconds": 0}


_SPANISH_ORDINAL_TO_SEASON = {
    "primera": 1,
    "segunda": 2,
    "tercera": 3,
    "cuarta": 4,
    "quinta": 5,
    "sexta": 6,
    "septima": 7,
    "séptima": 7,
    "octava": 8,
    "novena": 9,
    "decima": 10,
    "décima": 10,
}

_TECH_BLOCK_PATTERN = re.compile(
    r"(?i)(\d{3,4}x\d{3,4}|1080p|720p|2160p|1440p|4k|8k|x264|x265|h\.?264|h\.?265|hevc|av1|"
    r"blu-?ray|bdrip|bdremux|web-?dl|webrip|hdtv|dvd|audio\s*:|subs?\s*:|"
    r"flac|aac|ac3|dts|eac3|mp3|opus|mkv|mp4|avi|softsubs|hardsubs|hdr|dv|10bit|8bit|"
    r"esp|español|castellano|latino|dual|multi)"
)


# ------------------------------------------------------------
# Indica si el proveedor de IA necesita control de cuotas desde
# Kitsunarr o si se trata de un proveedor local sin límites remotos.
# ------------------------------------------------------------
def _supports_rate_limits(config: AIConfig) -> bool:
    return (config.provider or "").lower() != "ollama"


# ------------------------------------------------------------
# Construye la clave persistente proveedor/modelo usada por Kitsunarr
# para cuotas, estadísticas y pausas temporales de IA.
# ------------------------------------------------------------
def _rate_key(config: AIConfig) -> str:
    provider = (config.provider or "unknown").strip().lower()
    model = (config.model_name or "default").strip().lower()
    return f"{provider}:{model}"


# ------------------------------------------------------------
# Estima tokens de un texto para aplicar límites de uso antes de
# llamar al proveedor de IA configurado.
# ------------------------------------------------------------
def _estimate_tokens(text: str) -> int:
    if not text:
        return 1
    return max(1, (len(text) + 3) // 4)


# ------------------------------------------------------------
# Aplica límites RPM, TPM y RPD antes de llamar a la IA, actualizando
# los contadores persistidos por modelo en Kitsunarr.
# ------------------------------------------------------------
def _enforce_rate_limits_before_call(config: AIConfig, prompt: str) -> tuple[str | None, int, dict]:
    if not _supports_rate_limits(config):
        return (None, 0, {})

    key = _rate_key(config)
    now = datetime.now()
    prompt_tokens = _estimate_tokens(prompt)

    remaining = {}
    with Session(engine) as session:
        model = session.exec(select(AIModel).where(AIModel.model_key == key)).first()
        today = now.strftime("%Y-%m-%d")
        if not model:
            model = AIModel(
                model_key=key,
                provider=config.provider,
                model_name=(config.model_name or "").strip(),
                rpm_limit=int(config.rpm_limit or 4),
                tpm_limit=int(config.tpm_limit or 250000),
                rpd_limit=int(config.rpd_limit or 20),
                minute_window_start=now.isoformat(),
                minute_requests=0,
                minute_tokens=0,
                daily_date=today,
                daily_count=0,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
        else:
            model.rpm_limit = int(config.rpm_limit or model.rpm_limit or 4)
            model.tpm_limit = int(config.tpm_limit or model.tpm_limit or 250000)
            model.rpd_limit = int(config.rpd_limit or model.rpd_limit or 20)
            session.add(model)
            session.commit()

        if model.daily_date != today:
            model.daily_date = today
            model.daily_count = 0

        if not model.minute_window_start:
            model.minute_window_start = now.isoformat()
            model.minute_requests = 0
            model.minute_tokens = 0
        else:
            try:
                start = datetime.fromisoformat(model.minute_window_start)
            except Exception:
                start = now - timedelta(seconds=61)

            if now - start >= timedelta(seconds=60):
                model.minute_window_start = now.isoformat()
                model.minute_requests = 0
                model.minute_tokens = 0

        rpm_limit = max(1, int(model.rpm_limit or 1))
        tpm_limit = max(1, int(model.tpm_limit or 1))
        rpd_limit = int(model.rpd_limit or 0)

        if model.minute_requests >= rpm_limit:
            raise RuntimeError(
                f"Límite RPM alcanzado para {model.provider}/{model.model_name} ({model.minute_requests}/{rpm_limit}) en la última ventana de 60s."
            )

        if model.minute_tokens + prompt_tokens > tpm_limit:
            raise RuntimeError(
                f"Límite TPM alcanzado para {model.provider}/{model.model_name} ({model.minute_tokens + prompt_tokens}/{tpm_limit}) en la última ventana de 60s."
            )

        if rpd_limit and model.daily_count >= rpd_limit:
            raise RuntimeError(
                f"Límite diario alcanzado para {model.provider}/{model.model_name} ({model.daily_count}/{rpd_limit})."
            )

        model.minute_requests += 1
        model.minute_tokens += prompt_tokens
        model.daily_count += 1

        remaining = {
            "rpm_remaining": max(0, rpm_limit - model.minute_requests),
            "tpm_remaining": max(0, tpm_limit - model.minute_tokens),
            "rpd_remaining": max(0, (rpd_limit - model.daily_count)) if rpd_limit else None,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "rpd_limit": rpd_limit,
        }

        session.add(model)
        session.commit()
    return (key, prompt_tokens, remaining)


# ------------------------------------------------------------
# Registra los tokens aproximados de la respuesta de IA para mantener
# el control TPM del modelo usado por Kitsunarr.
# ------------------------------------------------------------
def _register_response_tokens(config: AIConfig, key: str | None, response_text: str) -> None:
    if not key or not _supports_rate_limits(config):
        return

    response_tokens = _estimate_tokens(response_text)
    now = datetime.now()
    with Session(engine) as session:
        model = session.exec(select(AIModel).where(AIModel.model_key == key)).first()
        if not model:
            return

        try:
            start = datetime.fromisoformat(model.minute_window_start) if model.minute_window_start else now - timedelta(seconds=61)
        except Exception:
            start = now - timedelta(seconds=61)

        if now - start >= timedelta(seconds=60):
            model.minute_window_start = now.isoformat()
            model.minute_requests = 0
            model.minute_tokens = 0

        model.minute_tokens = (model.minute_tokens or 0) + response_tokens
        session.add(model)
        session.commit()


# ------------------------------------------------------------
# Reinicia contadores diarios y ventanas de cuota de IA para todos
# los modelos o para un modelo concreto desde la UI.
# ------------------------------------------------------------
def reset_daily_quota(model_key: str | None = None):
    today = datetime.now().strftime("%Y-%m-%d")
    with Session(engine) as session:
        if model_key:
            row = session.exec(select(AIModel).where(AIModel.model_key == model_key)).first()
            if row:
                row.daily_date = today
                row.daily_count = 0
                row.minute_window_start = None
                row.minute_requests = 0
                row.minute_tokens = 0
                session.add(row)
        else:
            rows = session.exec(select(AIModel)).all()
            for row in rows:
                row.daily_date = today
                row.daily_count = 0
                row.minute_window_start = None
                row.minute_requests = 0
                row.minute_tokens = 0
                session.add(row)
        session.commit()
    logger.info("♻️ [IA] Contadores diarios y ventanas reiniciadas en BD.")


# ------------------------------------------------------------
# Extrae un ID de TheTVDB escrito en el texto devuelto por IA o en un
# título ya normalizado.
# ------------------------------------------------------------
def _extract_tvdb_id_from_text(text: str) -> str | None:
    if not text:
        return None

    m = re.search(r"(?i)tvdb\s*[-:_ ]\s*(\d+)", text)
    if m:
        return m.group(1)

    m = re.search(r"(?i)\[\s*tvdb\s*[-:_ ]\s*(\d+)\s*\]", text)
    if m:
        return m.group(1)

    return None


# ------------------------------------------------------------
# Asegura que el título final incluya el marcador TVDB aceptado en
# la posición que Sonarr entiende mejor.
# ------------------------------------------------------------
def _ensure_tvdb_marker_in_title(title: str, tvdb_id: str | None) -> str:
    if not title or not tvdb_id:
        return title

    marker = f"[tvdb-{tvdb_id}]"
    clean_title = re.sub(r"(?i)\s*\[\s*tvdb\s*[-:_ ]\s*\d+\s*\]\s*", " ", title)
    clean_title = re.sub(r"\s+", " ", clean_title).strip()

    season_matches = list(re.finditer(r"(?i)\bS\d{2}(?:\s*-\s*S?\d{2})?\b", clean_title))
    if season_matches:
        last = season_matches[-1]
        return f"{clean_title[:last.end()].strip()} {marker} {clean_title[last.end():].strip()}".strip()

    return f"{clean_title} {marker}".strip()


# ------------------------------------------------------------
# Extrae bloques técnicos del título para conservar calidad, codec,
# audios, subtítulos y contenedor fuera del razonamiento de IA.
# ------------------------------------------------------------
def _extract_technical_blocks(title: str) -> list[str]:
    if not title:
        return []
    blocks = re.findall(r"\[[^\]]+\]", title)
    return [b for b in blocks if _TECH_BLOCK_PATTERN.search(b)]


# ------------------------------------------------------------
# Retira del título los bloques técnicos antes de enviarlo a la IA
# para que el modelo razone solo sobre serie y temporada.
# ------------------------------------------------------------
def _strip_technical_blocks_for_ai(title: str) -> str:
    if not title:
        return ""

    # ------------------------------------------------------------
    # Decide si un bloque entre corchetes debe ocultarse al prompt de
    # IA por contener metadatos técnicos.
    # ------------------------------------------------------------
    def replace_block(match: re.Match) -> str:
        block = match.group(0)
        return "" if _TECH_BLOCK_PATTERN.search(block) else block

    cleaned = re.sub(r"\[[^\]]+\]", replace_block, title)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


# ------------------------------------------------------------
# Reincorpora al título normalizado por IA los metadatos técnicos que
# Kitsunarr había separado del título enriquecido.
# ------------------------------------------------------------
def _ensure_technical_metadata_in_title(translated_title: str, enriched_title: str) -> str:
    translated_title = (translated_title or "").strip()
    enriched_title = enriched_title or ""

    if not translated_title:
        return translated_title

    existing_blocks = _extract_technical_blocks(translated_title)
    source_blocks = _extract_technical_blocks(enriched_title)

    if source_blocks:
        existing_set = {b.lower() for b in existing_blocks}
        missing = [b for b in source_blocks if b.lower() not in existing_set]
        if missing:
            return f"{translated_title} {' '.join(missing)}".strip()

    return translated_title


# ------------------------------------------------------------
# Convierte ordinales españoles detectados en sinopsis o título al
# número de temporada correspondiente.
# ------------------------------------------------------------
def _season_number_from_word(word: str) -> int | None:
    normalized = (word or "").lower().strip()
    normalized = normalized.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    return _SPANISH_ORDINAL_TO_SEASON.get(normalized)


# ------------------------------------------------------------
# Encuentra el marcador SXX o SXX-SYY más fiable usando título y
# sinopsis del tracker antes de construir el título final.
# ------------------------------------------------------------
def _find_season_marker(title_text: str, description_text: str) -> str:
    title_text = title_text or ""
    description_text = description_text or ""
    sources = [title_text, description_text]

    for text in sources:
        range_match = re.search(r"(?i)\bS(\d{1,2})\s*[-/]\s*S?(\d{1,2})\b", text)
        if range_match:
            start = max(1, int(range_match.group(1)))
            end = max(start, int(range_match.group(2)))
            return f"S{start:02d}-S{end:02d}" if end > start else f"S{start:02d}"

    for text in sources:
        range_words = re.search(
            r"(?i)\b(?:temporadas?|temps?|seasons?)\s*(\d{1,2})\s*(?:-|/|a|to|hasta)\s*(\d{1,2})\b",
            text,
        )
        if range_words:
            start = max(1, int(range_words.group(1)))
            end = max(start, int(range_words.group(2)))
            return f"S{start:02d}-S{end:02d}" if end > start else f"S{start:02d}"

    for text in sources:
        single_match = re.search(r"(?i)\bS(\d{1,2})(?:E\d{1,3})?\b", text)
        if single_match:
            season = max(1, int(single_match.group(1)))
            return f"S{season:02d}"

    for text in sources:
        single_words = re.search(r"(?i)\b(?:temporada|temp\.?|season)\s*(\d{1,2})\b", text)
        if single_words:
            season = max(1, int(single_words.group(1)))
            return f"S{season:02d}"

        reverse_words = re.search(r"(?i)\b(\d{1,2})(?:a|ª|º)?\s*(?:temporada|season)\b", text)
        if reverse_words:
            season = max(1, int(reverse_words.group(1)))
            return f"S{season:02d}"

    ordinal_words = "primera|segunda|tercera|cuarta|quinta|sexta|septima|séptima|octava|novena|decima|décima"
    for text in sources:
        ord_match = re.search(rf"(?i)\b({ordinal_words})\s+(?:temporada|season)\b", text)
        if ord_match:
            season = _season_number_from_word(ord_match.group(1))
            if season:
                return f"S{season:02d}"

        ord_reverse = re.search(rf"(?i)\b(?:temporada|season)\s+({ordinal_words})\b", text)
        if ord_reverse:
            season = _season_number_from_word(ord_reverse.group(1))
            if season:
                return f"S{season:02d}"

    return "S01"


# ------------------------------------------------------------
# Inserta el marcador de temporada en el título final si todavía no
# aparece, preservando los bloques técnicos al final.
# ------------------------------------------------------------
def _ensure_season_marker_in_title(title: str, season_marker: str) -> str:
    if not title or not season_marker:
        return title
    if re.search(r"(?i)\bS\d{2}(?:\s*-\s*S?\d{2})?\b", title):
        return title

    tech_blocks = _extract_technical_blocks(title)
    if tech_blocks:
        first_tail = title.find(tech_blocks[0])
        if first_tail >= 0:
            return f"{title[:first_tail].strip()} {season_marker} {title[first_tail:].strip()}".strip()

    return f"{title.strip()} {season_marker}".strip()


# ------------------------------------------------------------
# Calcula temporada numérica y si el torrent es pack a partir del
# marcador de temporada ya normalizado.
# ------------------------------------------------------------
def _extract_season_info(title_text: str, description_text: str) -> tuple[int, bool]:
    marker = _find_season_marker(title_text, description_text)
    range_match = re.search(r"(?i)\bS(\d{2})\s*-\s*S?(\d{2})\b", marker)
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        return (start, end > start)

    single_match = re.search(r"(?i)\bS(\d{2})\b", marker)
    if single_match:
        return (int(single_match.group(1)), False)

    return (1, False)


# ------------------------------------------------------------
# Completa temporadas faltantes en fichas antiguas ya identificadas
# para que Sonarr reciba metadatos consistentes.
# ------------------------------------------------------------
def _backfill_parsed_season_for_identified_torrents(session: Session, limit: int = 200) -> None:
    rows = session.exec(
        select(TorrentCache)
        .where(TorrentCache.tvdb_status == "Listo")
        .where(TorrentCache.parsed_season == None)
        .where(TorrentCache.ai_translated_title != None)
        .limit(limit)
    ).all()
    if not rows:
        return

    fixed = 0
    for torrent in rows:
        season, is_batch = _extract_season_info(
            torrent.ai_translated_title or "",
            torrent.description or ""
        )
        torrent.parsed_season = season
        if is_batch:
            torrent.is_batch = True
        session.add(torrent)
        fixed += 1

    if fixed:
        session.commit()
        logger.info(f"🩹 [IA] Backfill de temporada aplicado a {fixed} torrents ya identificados.")


# ------------------------------------------------------------
# Renderiza el prompt personalizado del usuario sustituyendo los
# datos actuales de la ficha y los candidatos TVDB disponibles.
# ------------------------------------------------------------
def _render_custom_prompt(template: str, title: str, description: str, tvdb_candidates: list[dict]) -> str:
    candidates_json = json.dumps(tvdb_candidates, ensure_ascii=False) if tvdb_candidates else "No hay candidatos."
    rendered = template.replace("{title}", title or "")
    rendered = rendered.replace("{description}", description or "")
    rendered = rendered.replace("{tvdb_candidates}", candidates_json)
    rendered = rendered.replace('{tvdb_candidates if tvdb_candidates else "No hay candidatos."}', candidates_json)
    return rendered


# ------------------------------------------------------------
# Normaliza la respuesta JSON de la IA para el pipeline de Kitsunarr:
# título final, temporada, TVDB aceptado y metadatos técnicos.
# ------------------------------------------------------------
def _normalize_ai_output_for_pipeline(result: dict, fallback_title: str, description: str, tvdb_candidates: list[dict], enriched_title: str = None) -> dict:
    translated_title = (
        result.get("translated_title")
        or result.get("titulo_limpio")
        or fallback_title
    )

    season_context = " ".join([fallback_title or "", enriched_title or "", description or ""])
    season_marker = _find_season_marker(translated_title, season_context)
    translated_title = _ensure_season_marker_in_title(translated_title, season_marker)
    parsed_season, is_batch = _extract_season_info(translated_title, description or "")

    suggested_tvdb_id = result.get("tvdb_id") or _extract_tvdb_id_from_text(translated_title)
    suggested_tvdb_id = str(suggested_tvdb_id).strip() if suggested_tvdb_id is not None else None
    if suggested_tvdb_id and suggested_tvdb_id.lower() in ["none", "null", "", "0"]:
        suggested_tvdb_id = None

    candidate_ids = {str(c.get("tvdb_id")) for c in tvdb_candidates if c.get("tvdb_id")}
    accepted_tvdb_id = suggested_tvdb_id
    rejection_reason = None

    if suggested_tvdb_id and candidate_ids and suggested_tvdb_id not in candidate_ids:
        accepted_tvdb_id = None
        rejection_reason = f"ID fuera de candidatos permitidos: {suggested_tvdb_id}"

    translated_title = _ensure_tvdb_marker_in_title(translated_title, accepted_tvdb_id)
    translated_title = _ensure_technical_metadata_in_title(translated_title, enriched_title or fallback_title)

    return {
        "translated_title": translated_title,
        "parsed_season": parsed_season,
        "is_batch": is_batch,
        "suggested_tvdb_id": suggested_tvdb_id,
        "accepted_tvdb_id": accepted_tvdb_id,
        "candidate_ids": sorted(candidate_ids),
        "rejection_reason": rejection_reason,
    }


# ------------------------------------------------------------
# Procesa fichas pendientes con la IA configurada en Kitsunarr.
# Normaliza títulos, temporada y TVDB, respeta cuotas del proveedor
# y actualiza la caché local para Sonarr y la interfaz.
# ------------------------------------------------------------
async def process_pending_torrents(specific_guids: list[str] = None):
    global _ai_sleep_until_by_key

    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
        if not ai_config or not ai_config.is_enabled:
            return

        is_limited_provider = _supports_rate_limits(ai_config)
        current_model_key = _rate_key(ai_config)

        if not specific_guids and is_limited_provider:
            sleep_until = _ai_sleep_until_by_key.get(current_model_key)
            if sleep_until and datetime.now() < sleep_until:
                return
            if sleep_until and datetime.now() >= sleep_until:
                _ai_sleep_until_by_key.pop(current_model_key, None)

        if not is_limited_provider:
            _ai_sleep_until_by_key.pop(current_model_key, None)

        _backfill_parsed_season_for_identified_torrents(session)

        sys_config = session.exec(select(SystemConfig)).first()
        tvdb_worker_enabled = bool(sys_config and sys_config.tvdb_is_enabled and sys_config.tvdb_api_key)
            
        if not specific_guids and not ai_config.is_automated:
            return


        query = select(TorrentCache).where(
            (TorrentCache.ai_status == "Pendiente") | (TorrentCache.ai_status == "Error")
        )
        if specific_guids:
            guids = list(specific_guids)
        else:
            guids = None

        processed = 0
        while True:
            if guids:
                if not guids:
                    break
                guid = guids.pop(0)
                torrent = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
                if not torrent:
                    continue
            else:
                q = query
                if tvdb_worker_enabled:
                    q = q.where(
                        or_(
                            TorrentCache.tvdb_status == "Candidatos",
                            TorrentCache.tvdb_status == "Listo"
                        )
                    )
                torrent = session.exec(q.limit(1)).first()
                if not torrent:
                    if tvdb_worker_enabled and not specific_guids and processed == 0:
                        logger.info("⏳ [IA] Esperando candidatos TVDB antes de procesar nuevos torrents.")
                    break

            tvdb_candidates = _load_tvdb_candidates(session, torrent.guid)
            if tvdb_worker_enabled and not specific_guids and not tvdb_candidates:
                logger.info(f"⏳ [IA] {torrent.guid} en espera: TVDB aún sin candidatos.")
                break

            prompt = _build_ai_prompt(
                torrent.original_title,
                ai_config.custom_prompt,
                description=torrent.description,
                tvdb_candidates=tvdb_candidates,
                enriched_title=torrent.enriched_title,
            )

            try:
                raw_response = await _call_ai_provider(ai_config, prompt)

                json_match = re.search(r"```json\s*(.*?)\s*```", raw_response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    json_str = raw_response

                try:
                    result = json.loads(json_str)

                    normalized = _normalize_ai_output_for_pipeline(
                        result,
                        fallback_title=torrent.original_title,
                        description=torrent.description or "",
                        tvdb_candidates=tvdb_candidates,
                        enriched_title=torrent.enriched_title,
                    )

                    torrent.ai_translated_title = normalized["translated_title"]
                    torrent.ai_status = "Listo"

                    torrent.parsed_season = normalized["parsed_season"]
                    torrent.is_batch = normalized["is_batch"]

                    if normalized["rejection_reason"]:
                        logger.warning(
                            f"⚠️ [IA] tvdb_id descartado para {torrent.guid}: {normalized['rejection_reason']}"
                        )
                    
                    suggested_tvdb_id = normalized["accepted_tvdb_id"]
                    
                    if suggested_tvdb_id and str(suggested_tvdb_id).lower() not in ["none", "null", "", "0"]:
                        torrent.tvdb_id = str(suggested_tvdb_id)
                        torrent.tvdb_status = "Listo"
                        
                        existing_tvdb = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == torrent.tvdb_id)).first()
                        if not existing_tvdb or not existing_tvdb.is_full_record:
                            logger.info(f"📥 [IA] La IA vinculó TheTVDB ID {torrent.tvdb_id}. Descargando maestro...")
                            asyncio.create_task(fetch_full_tvdb_series(torrent.tvdb_id))
                    else:
                        if not torrent.tvdb_id and torrent.tvdb_status in ["No Encontrado", "Pendiente"]:
                            torrent.tvdb_status = "Pendiente"
                            
                    logger.info(f"✅ [IA] {torrent.guid} -> {torrent.ai_translated_title}")
                    
                except json.JSONDecodeError:
                    logger.error(f"❌ [IA] Respuesta de {ai_config.provider} no es JSON válido: {raw_response[:100]}...")
                    torrent.ai_status = "Error"
                    
            except Exception as e:
                logger.error(f"❌ [IA] Error con {ai_config.provider} en {torrent.guid}: {e}")
                torrent.ai_status = "Error"
                err_txt = str(e)
                if "Límite diario" in err_txt:
                    tomorrow = datetime.now() + timedelta(days=1)
                    _ai_sleep_until_by_key[current_model_key] = tomorrow.replace(hour=0, minute=1, second=0)
                    logger.warning(
                        f"⏳ [IA] Límite diario alcanzado para {current_model_key}. "
                        f"Suspendido hasta {_ai_sleep_until_by_key[current_model_key]}."
                    )
                    break
                if "Límite RPM alcanzado" in err_txt or "Límite TPM alcanzado" in err_txt:
                    _ai_sleep_until_by_key[current_model_key] = datetime.now() + timedelta(seconds=62)
                    logger.warning(
                        f"⏳ [IA] Límite por minuto alcanzado para {current_model_key}. "
                        f"Suspendido temporalmente hasta {_ai_sleep_until_by_key[current_model_key]}."
                    )
                    break
                if "Alta demanda del proveedor IA" in err_txt:
                    _ai_sleep_until_by_key[current_model_key] = datetime.now() + timedelta(minutes=3)
                    logger.warning(
                        f"⏳ [IA] Alta demanda detectada para {current_model_key}. "
                        f"Se pausa el modelo hasta {_ai_sleep_until_by_key[current_model_key]} antes de reintentar."
                    )
                    break
                if "Modelo IA no disponible" in err_txt:
                    _ai_sleep_until_by_key[current_model_key] = datetime.now() + timedelta(minutes=30)
                    logger.warning(
                        f"⏳ [IA] Modelo no disponible para {current_model_key}. "
                        f"Se pausa temporalmente hasta {_ai_sleep_until_by_key[current_model_key]}."
                    )
                    break

            finally:
                session.add(torrent)
                session.commit()
                processed += 1

# ------------------------------------------------------------
# Comprueba que Kitsunarr puede llamar al proveedor de IA configurado
# y recibir una respuesta JSON válida.
# ------------------------------------------------------------
async def test_ai_connection(config: AIConfig) -> dict:
    prompt = "Responde únicamente con un JSON válido que contenga la clave 'status' con el valor 'ok'."
    try:
        raw_response = await _call_ai_provider(config, prompt)
        
        json_match = re.search(r"```json\s*(.*?)\s*```", raw_response, re.DOTALL)
        if json_match:
            raw_response = json_match.group(1)
            
        result = json.loads(raw_response)
        if result.get("status") == "ok":
            return {"success": True}
        return {"success": False, "error": f"JSON no esperado: {raw_response}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ------------------------------------------------------------
# Ejecuta la normalización de IA sobre una ficha concreta y devuelve
# una vista previa sin modificar el torrent en la caché.
# ------------------------------------------------------------
async def test_single_torrent_ai(guid: str, config: AIConfig) -> dict:
    with Session(engine) as session:
        torrent = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not torrent:
            return {"success": False, "error": "Torrent no encontrado."}

        tvdb_candidates = _load_tvdb_candidates(session, torrent.guid)
        prompt = _build_ai_prompt(
            torrent.original_title,
            config.custom_prompt,
            description=torrent.description,
            tvdb_candidates=tvdb_candidates,
            enriched_title=torrent.enriched_title,
        )
        
        try:
            raw_response = await _call_ai_provider(config, prompt)
            json_match = re.search(r"```json\s*(.*?)\s*```", raw_response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = raw_response
                
            result = json.loads(json_str)

            normalized = _normalize_ai_output_for_pipeline(
                result,
                fallback_title=torrent.original_title,
                description=torrent.description or "",
                tvdb_candidates=tvdb_candidates,
                enriched_title=torrent.enriched_title,
            )

            preview = {
                "guid": torrent.guid,
                "original_title": torrent.original_title,
                "input_title": torrent.enriched_title,
                "description": torrent.description,
                "ai_translated_title": normalized["translated_title"],
                "parsed_season": normalized["parsed_season"],
                "is_batch": normalized["is_batch"],
                "ai_status": "Listo",
                "tvdb_status": "Listo" if normalized["accepted_tvdb_id"] else "Pendiente",
                "tvdb_resolution": {
                    "suggested_tvdb_id": normalized["suggested_tvdb_id"],
                    "accepted_tvdb_id": normalized["accepted_tvdb_id"],
                    "candidate_ids": normalized["candidate_ids"],
                    "rejection_reason": normalized["rejection_reason"],
                },
                "tvdb_candidates": tvdb_candidates,
            }

            return {
                "success": True,
                "result": result,
                "preview": preview,
                "raw_response": raw_response,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "raw": raw_response if 'raw_response' in locals() else ""}

# ------------------------------------------------------------
# Carga los candidatos TVDB vinculados a una ficha torrent para que
# el prompt de IA pueda elegir solo entre opciones permitidas.
# ------------------------------------------------------------
def _load_tvdb_candidates(session: Session, guid: str) -> list[dict]:
    links = session.exec(
        select(TorrentTVDBCandidates).where(TorrentTVDBCandidates.torrent_guid == guid)
    ).all()
    if not links:
        return []

    candidate_ids = [l.tvdb_id for l in links]
    series_rows = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id.in_(candidate_ids))).all()
    by_id = {s.tvdb_id: s for s in series_rows}

    candidates = []
    for candidate_id in candidate_ids:
        series = by_id.get(candidate_id)
        if series:
            aliases = []
            if series.aliases:
                try:
                    parsed_aliases = json.loads(series.aliases)
                    if isinstance(parsed_aliases, list):
                        aliases = [str(a) for a in parsed_aliases if a]
                except Exception:
                    aliases = []

            candidates.append({
                "tvdb_id": candidate_id,
                "name": series.series_name_es or series.series_name_original,
                "name_es": series.series_name_es,
                "name_original": series.series_name_original,
                "aliases": aliases,
                "overview": series.overview_es or series.overview_original or series.overview_basic,
                "first_aired": series.first_aired,
                "status": series.status,
            })
        else:
            candidates.append({"tvdb_id": candidate_id})

    return candidates[:8]


# ------------------------------------------------------------
# Construye el prompt maestro que Kitsunarr envía a la IA, usando el
# prompt personalizado cuando existe o el prompt interno por defecto.
# ------------------------------------------------------------
def _build_ai_prompt(original_title: str, custom_prompt: str = None, description: str = None, tvdb_candidates: list[dict] = None, enriched_title: str = None) -> str:
    tvdb_candidates = tvdb_candidates or []
    description = description or ""

    if custom_prompt:
        return _render_custom_prompt(custom_prompt, original_title, description, tvdb_candidates)

    candidates_json = json.dumps(tvdb_candidates, ensure_ascii=False) if tvdb_candidates else "No hay candidatos."
    title_for_ai = _strip_technical_blocks_for_ai(original_title) or original_title
    technical_blocks = _extract_technical_blocks(enriched_title or original_title)
    technical_note = " ".join(technical_blocks) if technical_blocks else "No hay bloques tecnicos detectados."

    return f"""
Eres un normalizador determinista de titulos de anime para Sonarr/Torznab.
Tu tarea es devolver un titulo final limpio y un tvdb_id. No expliques nada.

FORMATO OBJETIVO:
[Fansub intacto] Nombre exacto de la serie en TVDB SXX [tvdb-ID]

REGLAS OBLIGATORIAS:
1. Fansub:
   - Conserva intacto el primer bloque entre corchetes si aparece al inicio.
   - Ejemplo: [Union Fansub | Usuario] no se traduce, no se corrige y no se elimina.

2. Nombre de serie:
   - Si hay candidatos TVDB, compara el titulo y la sinopsis del tracker contra name, name_es, name_original, aliases y overview.
   - Si un candidato coincide, usa exactamente su campo "name" como nombre de serie.
   - Si "name" esta vacio, usa name_es; si tambien esta vacio, usa name_original.
   - No inventes titulos. No traduzcas a kanji, japones ni otro idioma si TVDB ya proporciona un nombre latino.
   - Si ningun candidato encaja claramente, conserva el nombre reconocible del tracker y usa tvdb_id null.

3. Temporada:
   - Siempre incluye temporada en formato SXX.
   - Si es pack de varias temporadas, usa SXX-SYY.
   - Prioridad de deteccion:
     a) titulo del tracker: S03, Season 3, Temporada 3, Temp. 3, 3a temporada, Temporadas 1-4.
     b) sinopsis del tracker: "tercera temporada", "segunda season", "temporada final 4".
     c) overview/sinopsis de candidatos TVDB solo como apoyo para confirmar la serie, no para inventar temporada.
   - Ordinales espanoles: primera=S01, segunda=S02, tercera=S03, cuarta=S04, quinta=S05, sexta=S06, septima=S07, octava=S08, novena=S09, decima=S10.
   - Si no hay ninguna pista de temporada, usa S01.

4. TVDB:
   - Si eliges un candidato, devuelve su tvdb_id y escribe [tvdb-ID] justo despues de la temporada.
   - No uses un ID que no este en la lista de candidatos.
   - Si no hay candidato fiable, devuelve tvdb_id null y no anadas marcador [tvdb-*].

5. Metadatos tecnicos:
   - No razones con codec, resolucion, audio, subtitulos o contenedor para elegir la serie.
   - No es necesario que los copies: Kitsunarr los reanadira despues de forma automatica.
   - Bloques tecnicos detectados fuera del prompt principal: {technical_note}

DATOS DE ENTRADA:
- Titulo limpio para razonar: {title_for_ai}
- Titulo original completo: {original_title}
- Sinopsis del tracker: {description}
- Candidatos TVDB JSON: {candidates_json}

EJEMPLOS:
Entrada titulo: [Union Fansub | User] Vaca y Pollo (Temporadas 1-4)
Candidato: {{"tvdb_id":"76196","name":"Vaca y Pollo","aliases":[]}}
Respuesta: {{"translated_title":"[Union Fansub | User] Vaca y Pollo S01-S04 [tvdb-76196]","tvdb_id":"76196"}}

Entrada titulo: [Union Fansub] Ataque a los Titanes
Sinopsis: En esta epica tercera temporada...
Candidato: {{"tvdb_id":"267440","name":"Ataque a los Titanes","aliases":["Shingeki no Kyojin","Attack on Titan"]}}
Respuesta: {{"translated_title":"[Union Fansub] Ataque a los Titanes S03 [tvdb-267440]","tvdb_id":"267440"}}

Entrada titulo: [Union Fansub] Shingeki no Kyojin
Candidato: {{"tvdb_id":"267440","name":"Ataque a los Titanes","aliases":["Shingeki no Kyojin"]}}
Respuesta: {{"translated_title":"[Union Fansub] Ataque a los Titanes S01 [tvdb-267440]","tvdb_id":"267440"}}

Responde UNICAMENTE con JSON puro valido:
{{
  "translated_title": "Titulo final",
  "tvdb_id": "ID o null"
}}
"""

    return f"""
Eres un experto en metadatos de anime para Sonarr. Tu misión es normalizar títulos de torrents.

REGLAS DE ORO:
1. [FANSUB]: Mantén el primer bloque de fansub intacto (ej. [UnionFansub | User]).
2. MATCH TVDB (PRIORIDAD ABSOLUTA):
   - Compara el Título Crudo con name y aliases de los candidatos.
   - Si hay coincidencia, usa el name EXACTO del candidato en alfabeto latino.
   - Prohibido traducir a kanji/japonés si el candidato está en alfabeto latino.
3. DETECCIÓN DE TEMPORADA Y PACKS:
   - Prioridad 1: título crudo.
   - Prioridad 2: sinopsis del tracker.
   - Formato packs: S01-S04.
   - Formato único: S02.
   - Si no se detecta temporada, usa S01.
4. TVDB: inserta [tvdb-ID] tras la temporada cuando exista ID confiable.
5. METADATOS TÉCNICOS: conserva los bloques técnicos finales tal y como están.

DATOS ACTUALES:
- Título Crudo: {original_title}
- Sinopsis del Tracker: {description}
- Candidatos TVDB: {candidates_json}

Responde ÚNICAMENTE con JSON puro:
{{
  "translated_title": "Título final",
  "tvdb_id": "ID o null"
 }}
"""

# ------------------------------------------------------------
# Llama al proveedor de IA configurado por Kitsunarr, aplica cuotas,
# gestiona errores conocidos y devuelve el texto bruto de respuesta.
# ------------------------------------------------------------
async def _call_ai_provider(config: AIConfig, prompt: str) -> str:
    decrypted_api_key = decrypt_secret(config.api_key) if config.api_key else ""
    provider = (config.provider or "").strip().lower()
    model = (config.model_name or "").strip()

    if provider in ["gemini", "openai"] and not decrypted_api_key.strip():
        logger.warning(f"⚠️ [WARNING][IA] No se puede enviar petición: falta API Key para {provider}/{model}.")
        raise RuntimeError("La API Key del proveedor IA no está configurada.")

    limiter_key, _, remaining = _enforce_rate_limits_before_call(config, prompt)
    if remaining:
        key = f"{provider}:{model}".lower()
        now = datetime.now()
        last_log = _ai_last_quota_log_by_key.get(key)
        rpm_rem = remaining.get("rpm_remaining")
        rpd_rem = remaining.get("rpd_remaining")
        milestone_values = {0, 5, 10, 15}
        should_log = (rpm_rem in milestone_values) or (rpd_rem in milestone_values if rpd_rem is not None else False)

        if should_log:
            if not (last_log and (now - last_log) < timedelta(seconds=3)):
                _ai_last_quota_log_by_key[key] = now
                rpd_txt = "∞" if remaining.get("rpd_remaining") is None else str(remaining.get("rpd_remaining"))
                logger.info(
                    f"ℹ️ [INFO][IA] Cuota restante {provider}/{model}: "
                    f"RPM {remaining.get('rpm_remaining')}/{remaining.get('rpm_limit')}, "
                    f"TPM {remaining.get('tpm_remaining')}/{remaining.get('tpm_limit')}, "
                    f"RPD {rpd_txt}/{remaining.get('rpd_limit') if remaining.get('rpd_limit') else '∞'}."
                )
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        if config.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.model_name.strip()}:generateContent?key={decrypted_api_key.strip()}"
            headers = {"Content-Type": "application/json"}
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                body = response.text or ""
                if response.status_code == 503 or "UNAVAILABLE" in body or "high demand" in body.lower():
                    logger.warning(f"⚠️ [WARNING][IA] Gemini en alta demanda para {model}. Reintenta en unos minutos.")
                    raise RuntimeError("Alta demanda del proveedor IA: el modelo está temporalmente saturado.")
                if response.status_code == 404 or "not found" in body.lower():
                    logger.error(f"❌ [ERROR][IA] Modelo no disponible en Gemini: {model}.")
                    raise RuntimeError("Modelo IA no disponible para este proveedor/versión API.")
                logger.error(f"❌ [ERROR][IA] Error Gemini ({response.status_code}) para {model}: {body}")
                response.raise_for_status()

            content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            _register_response_tokens(config, limiter_key, content)
            return content
            
        elif config.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {decrypted_api_key.strip()}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": config.model_name.strip(), 
                "messages": [{"role": "user", "content": prompt}]
            }
            
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                body = response.text or ""
                if response.status_code == 503 or "unavailable" in body.lower() or "overloaded" in body.lower():
                    logger.warning(f"⚠️ [WARNING][IA] OpenAI en alta demanda para {model}. Reintenta en unos minutos.")
                    raise RuntimeError("Alta demanda del proveedor IA: el modelo está temporalmente saturado.")
                if response.status_code in [400, 404] and ("model" in body.lower() and ("not found" in body.lower() or "does not exist" in body.lower())):
                    logger.error(f"❌ [ERROR][IA] Modelo no disponible en OpenAI: {model}.")
                    raise RuntimeError("Modelo IA no disponible para este proveedor.")
                logger.error(f"❌ [ERROR][IA] Error OpenAI ({response.status_code}) para {model}: {body}")
                response.raise_for_status()

            content = response.json()["choices"][0]["message"]["content"]
            _register_response_tokens(config, limiter_key, content)
            return content
            
        elif config.provider == "ollama":
            url = f"{config.base_url.rstrip('/')}/api/chat"
            payload = {"model": config.model_name.strip(), "messages": [{"role": "user", "content": prompt}], "stream": False}
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
            _register_response_tokens(config, limiter_key, content)
            return content
            
        else:
            raise ValueError(f"Proveedor '{config.provider}' no soportado.")
