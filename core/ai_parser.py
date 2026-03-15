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

Parámetros:
    specific_guids (list[str], opcional): Lista de GUIDs específicos a procesar. 
                                          Si se omite, buscará los pendientes automáticamente.
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

Parámetros:
    guid (str): Identificador único del torrent en la caché.
    title (str): Título original o enriquecido del torrent.
    description (str): Sinopsis extraída del tracker.
    config (AIConfig): Objeto de configuración con las credenciales a probar.

Retorna:
    str: Cadena de texto con el resultado JSON devuelto por la IA.
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

Parámetros:
    config (AIConfig): Objeto de configuración con los parámetros de red a probar.

Retorna:
    str: Respuesta directa generada por el modelo de lenguaje.
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

Parámetros:
    title (str): Título del torrent a procesar.
    description (str): Sinopsis proporcionada por el tracker.
    tvdb_candidates (str, opcional): Cadena JSON con posibles coincidencias de TheTVDB.
    custom_prompt (str, opcional): Plantilla personalizada definida por el usuario.

Retorna:
    str: El prompt final formateado y listo para el LLM.
"""
def _build_single_prompt(title: str, description: str, tvdb_candidates: str = None, custom_prompt: str = None) -> str:
    if custom_prompt and custom_prompt.strip():
        prompt = custom_prompt
        prompt = prompt.replace("{title}", title)
        prompt = prompt.replace("{description}", description)
        prompt = prompt.replace("{tvdb_candidates}", tvdb_candidates if tvdb_candidates else "Sin coincidencias en TVDB.")
        return prompt

    prompt = f"""
Eres un experto en organizar metadatos de anime para Sonarr. 
Tu objetivo es normalizar el título de un torrent manteniendo INTEGRALMENTE los datos técnicos y aplicando reglas estrictas de nomenclatura.

REGLAS CRÍTICAS DE PROCESAMIENTO:
1. [FANSUB]: Mantén el primer bloque de fansub exactamente como está.
2. TÍTULO OFICIAL: 
   - Extrae el nombre de la serie.
   - ¡IMPORTANTE!: Si seleccionas un ID de TVDB de la lista de candidatos, DEBES renombrar la serie EXACTAMENTE con el 'name' oficial que aparece en la opción de TVDB elegida.
3. TEMPORADAS Y PACKS:
   - Busca en el título y en la sinopsis menciones a "Primera Temporada", "Segunda Temporada", etc. o formatos similares. 
   - Si es un PACK o rango (Ej: "Temporadas 1-4"), conviértelo al estándar de Sonarr: "S01-S04".
   - Si es una única temporada, conviértela al formato SXX (S01, S02...). 
   - Si no hay mención y NO es un pack, usa S01 por defecto.
4. [tvdb-ID]: Si seleccionas un ID de TVDB, insértalo como [tvdb-XXXXXX] justo después de la temporada/pack.
5. METADATOS TÉCNICOS: Conserva TODOS los bloques entre corchetes del final (resolución, códecs, audios, subs). ¡PROHIBIDO BORRARLOS O RESUMIRLOS!

DATOS PARA PROCESAR:
- Título Crudo: {title}
- Sinopsis del Tracker: {description}
"""
    if tvdb_candidates:
        prompt += f"""
CANDIDATOS DE TVDB:
{tvdb_candidates}

REGLA TVDB: Si hay coincidencia, devuelve su 'tvdb_id'. Usa el 'name' de TVDB como título principal.
"""

    prompt += """
EJEMPLOS DE ÉXITO:

Entrada (Pack):
Título: [UnionFansub | sempai23] Vaca y Pollo (Temporadas 1-4) [MPEG2, 728x544 (DVD)] [Audio: Castellano, Latino]
Salida: {
    "translated_title": "[UnionFansub | sempai23] Vaca y Pollo S01-S04 [tvdb-76196] [MPEG2, 728x544 (DVD)] [Audio: Castellano, Latino]",
    "tvdb_id": "76196"
}

Entrada (Normalización Nombre):
Título: [UnionFansub | Unmei] Campione!: Matsurowanu Kamigami to Kamigoroshi no Maou [1080p] [Jap-Esp]
Opciones TVDB: [{"tvdb_id": "259646", "name": "Campione!"}]
Salida: {
    "translated_title": "[UnionFansub | Unmei] Campione! S01 [tvdb-259646] [1080p] [Jap-Esp]",
    "tvdb_id": "259646"
}

FORMATO DE SALIDA: Responde ÚNICAMENTE con JSON puro.
{
    "translated_title": "Título final",
    "tvdb_id": "ID en string o null"
}
"""
    return prompt

"""
Limpia la respuesta de texto devuelta por el LLM, eliminando bloques de código 
Markdown (```json) o caracteres adicionales, e intenta convertirla en un diccionario.

Parámetros:
    raw_text (str): Texto crudo devuelto por la Inteligencia Artificial.

Retorna:
    dict: Diccionario estructurado con el título traducido y el ID de TVDB.

Excepciones:
    ValueError: Si la respuesta final no es un JSON válido o parseable.
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

Parámetros:
    prompt (str): Texto completo con las instrucciones para el LLM.
    config (AIConfig): Objeto con las credenciales, URL base y nombre del modelo.

Retorna:
    str: El contenido en texto puro generado por el LLM.
"""
async def call_ai_provider(prompt: str, config: AIConfig) -> str:
    async with httpx.AsyncClient(timeout=45.0) as client:
        if config.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.model_name}:generateContent?key={config.api_key}"
            resp = await client.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            
        elif config.provider == "openai":
            url = f"https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {config.api_key}"}
            payload = {"model": config.model_name, "messages": [{"role": "user", "content": prompt}]}
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
            
        elif config.provider == "ollama":
            url = f"{config.base_url.rstrip('/')}/api/chat"
            payload = {"model": config.model_name, "messages": [{"role": "user", "content": prompt}], "stream": False}
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
            
        else:
            raise ValueError("Proveedor no soportado.")
