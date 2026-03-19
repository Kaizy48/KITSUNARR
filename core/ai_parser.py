# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import json
import re
import asyncio
from datetime import datetime, timedelta

import httpx
from sqlmodel import Session, select, delete

from core.logger import logger
from core.database import engine
from core.models.system import AIConfig, SystemConfig
from core.models.torrent import TorrentCache, TVDBCache, TorrentTVDBCandidates
from services.adapters.tvdb_scraper import fetch_full_tvdb_series, fetch_tvdb_episodes 

# Variables globales en memoria RAM (Circuit Breaker)
_ai_sleep_until = None
_ram_daily_count = 0
_ram_last_date = None


# ==========================================
# PROCESAMIENTO PRINCIPAL DE TORRENTS
# ==========================================

"""
Procesa de forma automatizada o manual los torrents en estado 'Pendiente' utilizando el 
proveedor de Inteligencia Artificial configurado. Implementa bloqueos por límites de cuota 
diaria (Circuit Breaker en RAM) y control de velocidad (RPM). Cruza la información obtenida 
con los candidatos de TheTVDB y actualiza la base de datos relacional.
"""
async def process_pending_torrents(specific_guids: list[str] = None):
    global _ai_sleep_until, _ram_daily_count, _ram_last_date
    
    if not specific_guids and _ai_sleep_until:
        if datetime.utcnow() < _ai_sleep_until:
            return
        else:
            _ai_sleep_until = None
            
    today = datetime.utcnow().date()
    if _ram_last_date != today:
        _ram_daily_count = 0
        _ram_last_date = today

    torrents_to_process = []
    ai_config_data = None
    sys_config_data = None
    delay_between_requests = 0
    
    with Session(engine) as session:
        config = session.exec(select(AIConfig)).first()
        sys_config = session.exec(select(SystemConfig)).first()
        
        if not config or not config.is_enabled: return 
        if not specific_guids and not config.is_automated: return 
        
        if not specific_guids and _ram_daily_count >= config.rpd_limit:
            logger.warning(f"⏸️ Límite diario de IA en RAM alcanzado ({_ram_daily_count}/{config.rpd_limit}). IA a dormir hasta mañana.")
            _ai_sleep_until = datetime.utcnow() + timedelta(hours=24)
            return
        
        ai_config_data = AIConfig(
            provider=config.provider, model_name=config.model_name, 
            api_key=config.api_key, base_url=config.base_url, custom_prompt=config.custom_prompt
        )
        sys_config_data = SystemConfig(
            tvdb_api_key=sys_config.tvdb_api_key, tvdb_is_enabled=sys_config.tvdb_is_enabled
        ) if sys_config else None
        
        if specific_guids:
            pending_torrents = session.exec(select(TorrentCache).where(TorrentCache.guid.in_(specific_guids))).all()
        else:
            delay_between_requests = 60.0 / config.rpm_limit if config.rpm_limit > 0 else 0
            
            query = select(TorrentCache).where(TorrentCache.ai_status == "Pendiente")
            if sys_config and sys_config.tvdb_api_key and sys_config.tvdb_is_enabled:
                query = query.where(TorrentCache.tvdb_status == "Candidatos")
            
            pending_torrents = session.exec(query.limit(5)).all()
        
        if not pending_torrents: return 
        
        for t in pending_torrents:
            candidates_db = session.exec(
                select(TVDBCache)
                .join(TorrentTVDBCandidates)
                .where(TorrentTVDBCandidates.torrent_guid == t.guid)
            ).all()
            
            candidates_list = []
            for c in candidates_db:
                aliases_list = json.loads(c.aliases) if c.aliases else []
                candidates_list.append({
                    "tvdb_id": str(c.tvdb_id), "name": c.series_name_es,
                    "aliases": aliases_list, "year": c.first_aired, "overview": c.overview_basic
                })
            
            torrents_to_process.append({
                "guid": t.guid, "enriched_title": t.enriched_title,
                "description": t.description, "candidates_list": candidates_list
            })
            
    success_count = 0
    
    for i, t_data in enumerate(torrents_to_process):
        if not specific_guids and _ram_daily_count >= config.rpd_limit:
            logger.warning(f"⏸️ Límite diario alcanzado en medio del lote ({_ram_daily_count}/{config.rpd_limit}). Abortando el resto.")
            _ai_sleep_until = datetime.utcnow() + timedelta(hours=24)
            break

        if i > 0 and delay_between_requests > 0:
            logger.info(f"⏱️ Control RPM: Esperando {delay_between_requests:.1f}s para no saturar la API...")
            await asyncio.sleep(delay_between_requests)
            
        logger.info(f"🧠 Enviando a IA: '{t_data['enriched_title']}' (GUID: {t_data['guid']})")
        
        candidates_json_str = json.dumps(t_data["candidates_list"], ensure_ascii=False) if t_data["candidates_list"] else None
        
        prompt = _build_single_prompt(
            title=t_data["enriched_title"], 
            description=t_data["description"] or "Sin descripción", 
            tvdb_candidates=candidates_json_str, custom_prompt=ai_config_data.custom_prompt
        )
        
        try:
            raw_result = await call_ai_provider(prompt, ai_config_data)
            parsed_data = _clean_and_parse_json(raw_result)
            
            translated_title = parsed_data.get("translated_title")
            chosen_tvdb_id = parsed_data.get("tvdb_id")
            
            fetch_tvdb_needed = False
            tvdb_id_to_fetch = None

            with Session(engine) as session:
                t = session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                if t:
                    if translated_title:
                        t.ai_translated_title = translated_title
                        t.ai_status = "Listo"
                    
                    if chosen_tvdb_id and str(chosen_tvdb_id).lower() not in ["null", "none", ""]:
                        t.tvdb_id = str(chosen_tvdb_id)
                        t.tvdb_status = "Listo"
                        
                        session.exec(delete(TorrentTVDBCandidates).where(TorrentTVDBCandidates.torrent_guid == t.guid))
                        
                        if sys_config_data and sys_config_data.tvdb_is_enabled and sys_config_data.tvdb_api_key:
                            fetch_tvdb_needed = True
                            tvdb_id_to_fetch = t.tvdb_id
                            
                    elif t_data["candidates_list"]:
                        t.tvdb_status = "Revisión Manual"
                    
                    session.commit()
                    success_count += 1
                    _ram_daily_count += 1
                    logger.info(f"✅ Respuesta IA: '{translated_title}' | TVDB ID: {chosen_tvdb_id}")

            if fetch_tvdb_needed and tvdb_id_to_fetch:
                await fetch_full_tvdb_series(tvdb_id_to_fetch, sys_config_data)
                await fetch_tvdb_episodes(tvdb_id_to_fetch, sys_config_data)

        except httpx.HTTPStatusError as http_err:
            status = http_err.response.status_code
            logger.error(f"❌ Error crítico de la API de IA (HTTP {status}): {http_err.response.text}")
            
            if status in [401, 403, 429]:
                logger.critical("🛑 MOTOR IA DESACTIVADO EN RAM. El proveedor rechazó la conexión. Worker a dormir 24h.")
                _ai_sleep_until = datetime.utcnow() + timedelta(hours=24)
                return 
                
            with Session(engine) as session:
                try:
                    t = session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                    if t:
                        t.ai_status = "Error"
                        session.commit()
                except Exception:
                    session.rollback()

        except Exception as e:
            logger.error(f"❌ Error procesando el torrent {t_data['guid']} con IA: {e}")
            with Session(engine) as session:
                try:
                    t = session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                    if t:
                        t.ai_status = "Error"
                        session.commit()
                except Exception as inner_e:
                    session.rollback()
                    
            if specific_guids:
                raise e
                
    if not specific_guids and success_count > 0:
        logger.info(f"✅ Ciclo de IA completado ({success_count}/{len(torrents_to_process)}).")


# ==========================================
# HERRAMIENTAS DE PRUEBA Y DIAGNÓSTICO
# ==========================================

"""
Ejecuta una prueba aislada del motor de Inteligencia Artificial sobre un torrent específico. 
Ensambla el prompt con sus metadatos y candidatos de TheTVDB, lo envía al proveedor y 
devuelve la respuesta en formato JSON sin persistir ningún cambio en la base de datos.
"""
async def test_single_torrent_ai(guid: str, title: str, description: str, config: AIConfig) -> str:
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not t:
            raise Exception("Torrent no encontrado")
            
        candidates_db = session.exec(
            select(TVDBCache)
            .join(TorrentTVDBCandidates)
            .where(TorrentTVDBCandidates.torrent_guid == t.guid)
        ).all()
        
        candidates_list = []
        for c in candidates_db:
            aliases_list = json.loads(c.aliases) if c.aliases else []
            candidates_list.append({
                "tvdb_id": str(c.tvdb_id), "name": c.series_name_es,
                "aliases": aliases_list, "year": c.first_aired, "overview": c.overview_basic
            })
        
        candidates_json_str = json.dumps(candidates_list, ensure_ascii=False) if candidates_list else None

    prompt = _build_single_prompt(t.enriched_title, t.description or "", candidates_json_str, config.custom_prompt)
    try:
        raw_result = await call_ai_provider(prompt, config)
        parsed_data = _clean_and_parse_json(raw_result)
        return json.dumps(parsed_data, indent=2, ensure_ascii=False)
    except Exception as e:
        raise Exception(f"Fallo en la prueba: {str(e)}")

"""
Realiza un ping o petición básica de prueba al proveedor de Inteligencia Artificial.
Sirve para verificar la conectividad de red con el servidor (local o remoto) y 
validar que las credenciales (API Key) proporcionadas son correctas.
"""
async def test_ai_connection(config: AIConfig) -> str:
    prompt = "Petición de testeo: responde únicamente con la frase exacta 'Estoy escuchando.' sin comillas ni texto adicional."
    try:
        result = await call_ai_provider(prompt, config)
        return result.strip()
    except Exception as e:
        raise Exception(f"Error de conexión: {str(e)}")


# ==========================================
# FUNCIONES AUXILIARES (PROMPTS Y PARSEO)
# ==========================================

"""
Construye la cadena de texto estructurada (prompt) que será evaluada por el modelo de IA.
Inyecta dinámicamente el título, la descripción y los candidatos de TheTVDB.
Soporta la sustitución completa de la plantilla si el usuario ha definido un prompt personalizado.
"""
def _build_single_prompt(title: str, description: str, tvdb_candidates: str = None, custom_prompt: str = None) -> str:
    if custom_prompt and custom_prompt.strip():
        return custom_prompt.replace("{title}", title).replace("{description}", description).replace("{tvdb_candidates}", tvdb_candidates or "Sin candidatos.")

    prompt = f"""
Eres un experto en metadatos de anime para Sonarr. Tu misión es normalizar títulos de torrents.

REGLAS DE ORO:
1. [FANSUB]: Mantén el primer bloque de fansub intacto (ej. [UnionFansub | User]).
2. MATCH TVDB (PRIORIDAD ABSOLUTA): 
   - Compara el 'Título Crudo' con el 'name' y la lista de 'aliases' de los candidatos.
   - Si hay coincidencia, usa el 'name' EXACTO del candidato.
   - ¡ESTRICTAMENTE PROHIBIDO traducir el nombre a Kanji o Japonés! Escríbelo usando el alfabeto latino exactamente como aparece en el JSON.
3. DETECCIÓN DE TEMPORADA Y PACKS:
   - Prioridad 1: Buscar en el Título Crudo.
   - Prioridad 2: Si no está en el título, busca en la Sinopsis del Tracker (ej. "Tercera Temporada" -> S03).
   - Formato Pack: "Temporadas 1-4" -> "S01-S04".
   - Formato Único: "Temporada 2" -> "S02". 
   - En caso de no encontrar coincidencia de temporada, por defecto usa S01.
4. TVDB: Inserta [tvdb-ID] justo después de la temporada.
5. METADATOS TÉCNICOS: Mantén todos los corchetes finales [Codec, Calidad, Audio, Subs] exactamente como están.

DATOS ACTUALES:
- Título Crudo: {title}
- Sinopsis del Tracker: {description}
CANDIDATOS TVDB: {tvdb_candidates if tvdb_candidates else "No hay candidatos."}

EJEMPLOS DE ÉXITO:

ESCENARIO 1: PACK DE TEMPORADAS
Entrada: [UnionFansub | User] Vaca y Pollo (Temporadas 1-4) [DVD] [Esp]
Candidato: {{"tvdb_id": "76196", "name": "Vaca y Pollo"}}
Salida: {{
    "translated_title": "[UnionFansub | User] Vaca y Pollo S01-S04 [tvdb-76196] [DVD] [Esp]",
    "tvdb_id": "76196"
}}

ESCENARIO 2: TEMPORADA EN SINOPSIS (Título sin temporada)
Entrada Título: [UnionFansub] Ataque a los Titanes [1080p]
Sinopsis: "...en esta épica Tercera Temporada de la serie..."
Candidato: {{"tvdb_id": "267440", "name": "Ataque a los Titanes"}}
Salida: {{
    "translated_title": "[UnionFansub] Ataque a los Titanes S03 [tvdb-267440] [1080p]",
    "tvdb_id": "267440"
}}

ESCENARIO 3: MATCH POR ALIAS (Japonés -> Español)
Entrada: [UnionFansub] Shingeki no Kyojin [720p]
Candidato: {{"tvdb_id": "267440", "name": "Ataque a los Titanes", "aliases": ["Shingeki no Kyojin", "Attack on Titan"]}}
Salida: {{
    "translated_title": "[UnionFansub] Ataque a los Titanes S01 [tvdb-267440] [720p]",
    "tvdb_id": "267440"
}}

Responde ÚNICAMENTE con JSON puro:
{{
    "translated_title": "Título final",
    "tvdb_id": "ID o null"
}}"""
    return prompt

"""
Limpia la respuesta en texto bruto generada por el proveedor de Inteligencia Artificial.
Filtra cualquier formato adicional (como bloques markdown de código) para aislar 
y decodificar exclusivamente el objeto JSON válido devuelto por el LLM.
"""
def _clean_and_parse_json(raw_text: str) -> dict:
    clean_text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
    clean_text = re.sub(r"```$", "", clean_text.strip()).strip()
    try:
        return json.loads(clean_text)
    except json.JSONDecodeError:
        logger.error(f"❌ Error parseando JSON de IA. Texto recibido: \n{clean_text}")
        raise ValueError("La IA no devolvió un JSON válido.")


# ==========================================
# COMUNICACIÓN CON APIS EXTERNAS
# ==========================================

"""
Cliente HTTP multiproveedor para las peticiones de Inteligencia Artificial.
Construye la solicitud HTTP (URL, cabeceras, payload) con la estructura específica 
requerida por cada API soportada (Gemini, OpenAI, Ollama) y extrae el texto resultante.
"""
async def call_ai_provider(prompt: str, config: AIConfig) -> str:
    timeout = httpx.Timeout(45.0, connect=10.0)
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        if config.provider == "gemini":
            model_clean = config.model_name.strip().replace("models/", "")
            
            url = f"https://generativelanguage.googleapis.com/v1/models/{model_clean}:generateContent"
            params = {"key": config.api_key.strip()}
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            
            response = await client.post(url, params=params, json=payload)
            
            if response.status_code != 200:
                logger.error(f"❌ Error Gemini ({response.status_code}): {response.text}")
                response.raise_for_status()
                
            return response.json()["candidates"][0]["content"]["parts"][0]["text"]
            
        elif config.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {config.api_key.strip()}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": config.model_name.strip(), 
                "messages": [{"role": "user", "content": prompt}]
            }
            
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 200:
                logger.error(f"❌ Error OpenAI ({response.status_code}): {response.text}")
                response.raise_for_status()
                
            return response.json()["choices"][0]["message"]["content"]
            
        elif config.provider == "ollama":
            url = f"{config.base_url.rstrip('/')}/api/chat"
            payload = {"model": config.model_name.strip(), "messages": [{"role": "user", "content": prompt}], "stream": False}
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
            
        else:
            raise ValueError(f"Proveedor '{config.provider}' no soportado.")