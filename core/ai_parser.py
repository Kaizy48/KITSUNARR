# ==========================================
# MOTOR DE INTELIGENCIA ARTIFICIAL (IA PARSER)
# ==========================================
import httpx
import json
import re
from core.logger import logger
from sqlmodel import Session, select
from core.database import engine
from core.models.torrent import TorrentCache
from core.models.system import AIConfig, SystemConfig


# ==========================================
# PROCESAMIENTO AUTOMÁTICO INDIVIDUAL (1 a 1)
# ==========================================

"""
Procesa de forma automatizada o manual un lote de torrents pendientes, utilizando 
el proveedor de Inteligencia Artificial configurado. Recupera metadatos, cruza 
información con candidatos de TheTVDB y actualiza la base de datos con los resultados.
"""
async def process_pending_torrents(specific_guids: list[str] = None):
    with Session(engine) as session:
        config = session.exec(select(AIConfig)).first()
        sys_config = session.exec(select(SystemConfig)).first()
        
        if not config or not config.is_enabled: return 
        if not specific_guids and not config.is_automated: return 
        
        if specific_guids:
            pending_torrents = session.exec(select(TorrentCache).where(TorrentCache.guid.in_(specific_guids)).limit(5)).all()
        else:
            query = select(TorrentCache).where(TorrentCache.ai_status == "Pendiente")
            if sys_config and sys_config.tvdb_api_key and sys_config.tvdb_is_enabled:
                query = query.where(TorrentCache.tvdb_status != "Pendiente")
            pending_torrents = session.exec(query.limit(5)).all()
            
        if not pending_torrents: return 
        
        success_count = 0
        
        for t in pending_torrents:
            logger.info(f"🧠 Enviando a IA: '{t.enriched_title}' (GUID: {t.guid})")
            
            prompt = _build_single_prompt(
                title=t.enriched_title, 
                description=t.description or "Sin descripción", 
                tvdb_candidates=t.tvdb_candidates,
                custom_prompt=config.custom_prompt
            )
            
            try:
                raw_result = await call_ai_provider(prompt, config)
                parsed_data = _clean_and_parse_json(raw_result)
                
                translated_title = parsed_data.get("translated_title")
                chosen_tvdb_id = parsed_data.get("tvdb_id")
                
                if translated_title:
                    t.ai_translated_title = translated_title
                    t.ai_status = "Listo"
                
                if chosen_tvdb_id and str(chosen_tvdb_id).lower() not in ["null", "none"]:
                    t.tvdb_id = str(chosen_tvdb_id)
                    t.tvdb_status = "Listo"
                elif t.tvdb_candidates:
                    t.tvdb_status = "Revisión Manual"
                
                session.commit()
                success_count += 1
                logger.info(f"✅ Respuesta IA: '{translated_title}' | TVDB ID: {chosen_tvdb_id}")
                
            except Exception as e:
                logger.error(f"❌ Error procesando el torrent {t.guid} con IA: {e}")
                t.ai_status = "Error"
                session.commit()
                if specific_guids:
                    raise e
                    
        if not specific_guids and success_count > 0:
            logger.info(f"✅ Ciclo de procesamiento completado ({success_count}/{len(pending_torrents)}).")

"""
Realiza una prueba aislada del motor de IA con un torrent específico, sin guardar 
los cambios en la base de datos. Se utiliza en el entorno de pruebas de la interfaz web.
"""
async def test_single_torrent_ai(guid: str, title: str, description: str, config: AIConfig) -> str:
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        candidates = t.tvdb_candidates if t else None

    prompt = _build_single_prompt(t.enriched_title, t.description, t.tvdb_candidates, config.custom_prompt)
    try:
        raw_result = await call_ai_provider(prompt, config)
        parsed_data = _clean_and_parse_json(raw_result)
        return json.dumps(parsed_data, indent=2, ensure_ascii=False)
    except Exception as e:
        raise Exception(f"Fallo en la prueba: {str(e)}")

"""
Ejecuta un ping básico al proveedor de Inteligencia Artificial para verificar 
la validez de la clave API o la conexión con el servidor local.
"""
async def test_ai_connection(config: AIConfig) -> str:
    prompt = "Petición de testeo: responde únicamente con la frase exacta 'Estoy escuchando.' sin comillas ni texto adicional."
    try:
        result = await call_ai_provider(prompt, config)
        return result.strip()
    except Exception as e:
        raise Exception(f"Error de conexión: {str(e)}")


"""
Construye el prompt definitivo que se enviará al modelo de lenguaje. 
Reemplaza las variables mágicas del prompt personalizado del usuario o utiliza 
la plantilla maestra del sistema para inyectar títulos, descripciones y candidatos TVDB.
"""
def _build_single_prompt(title: str, description: str, tvdb_candidates: str = None, custom_prompt: str = None) -> str:
    if custom_prompt and custom_prompt.strip():
        return custom_prompt.replace("{title}", title).replace("{description}", description).replace("{tvdb_candidates}", tvdb_candidates or "Sin candidatos.")

    prompt = f"""
Eres un experto en metadatos de anime para Sonarr. Tu misión es normalizar títulos de torrents.

REGLAS DE ORO:
1. [FANSUB]: Mantén el primer bloque de fansub intacto (ej. [UnionFansub | User]).
2. MATCH TVDB (PRIORIDAD ESPAÑOL): 
   - Compara el 'Título Crudo' con el 'name' y la lista de 'aliases' de los candidatos.
   - Si hay coincidencia, usa el 'name' del candidato (que está en español) para el título final.
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
Limpia la respuesta de texto devuelta por el LLM, eliminando bloques de código 
Markdown (```json) o caracteres adicionales, e intenta convertirla en un diccionario.
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
Realiza la llamada HTTP asíncrona al proveedor de IA correspondiente 
adaptando el formato de la petición a las especificaciones de cada API.
"""
async def call_ai_provider(prompt: str, config: AIConfig) -> str:
    """
    Realiza la llamada HTTP asíncrona a la API estable (v1) de Gemini o OpenAI.
    """
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

async def test_ai_connection(config: AIConfig) -> str:
    """
    Lanza un test de conexión y, en caso de fallo 404, intenta listar los modelos 
    disponibles para ayudar al usuario a diagnosticar el error.
    """
    try:
        prompt = "Responde únicamente: OK"
        return await call_ai_provider(prompt, config)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404 and config.provider == "gemini":
            diag_url = f"https://generativelanguage.googleapis.com/v1/models?key={config.api_key.strip()}"
            async with httpx.AsyncClient() as client:
                diag_resp = await client.get(diag_url)
                logger.error(f"🔍 [DIAGNÓSTICO GEMINI] Tu API Key tiene acceso a estos modelos: {diag_resp.text}")
            raise Exception("Error 404: El modelo no existe o no tienes acceso. Revisa el log para ver la lista de modelos disponibles.")
        raise e
