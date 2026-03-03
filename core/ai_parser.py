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
from core.models.system import AIConfig


# ==========================================
# PROCESAMIENTO AUTOMÁTICO Y POR LOTES
# ==========================================

async def process_pending_torrents(specific_guids: list[str] = None):
    """
    Función principal de orquestación de la IA.
    Extrae torrents "Pendientes" de la caché, los empaqueta en un JSON,
    se los envía al LLM y guarda los resultados limpios de vuelta en la base de datos.
    Puede ejecutarse automáticamente por el 'worker' o manualmente desde la UI (pasando specific_guids).
    """
    with Session(engine) as session:
        config = session.exec(select(AIConfig)).first()
        
        # 1. Comprobaciones de seguridad y estado
        if not config or not config.is_enabled:
            if specific_guids:
                raise Exception("El Motor General de IA está apagado. Enciéndelo para procesar lotes.")
            return 
        
        if not specific_guids and not config.is_automated:
            return 
        
        # 2. Selección de los torrents a procesar (Lotes de 5 para no saturar el prompt)
        if specific_guids:
            pending_torrents = session.exec(select(TorrentCache).where(TorrentCache.guid.in_(specific_guids)).limit(5)).all()
        else:
            pending_torrents = session.exec(select(TorrentCache).where(TorrentCache.ai_status == "Pendiente").limit(5)).all()
            
        if not pending_torrents:
            return 
        
        # 3. Preparación del payload para la IA
        data_to_send = {}
        for t in pending_torrents:
            data_to_send[t.guid] = {"titulo_actual": t.enriched_title, "sinopsis": t.description}
            logger.info(f"🧠 Enviando petición para Título Enriquecido con IA de Torrent \"{t.guid}\" \"{t.enriched_title}\"")

        prompt = _build_prompt(data_to_send)

        # 4. Llamada al proveedor y actualización de la BD
        try:
            result_json = await call_ai_provider(prompt, config)
            parsed_data = _clean_and_parse_json(result_json)
            
            success_count = 0
            for guid, new_title in parsed_data.items():
                t = session.exec(select(TorrentCache).where(TorrentCache.guid == str(guid))).first()
                if t:
                    t.ai_translated_title = new_title
                    t.ai_status = "Listo" 
                    success_count += 1
                    logger.info(f"🧠 Recibida petición para Título Enriquecido con IA de Torrent \"{guid}\" \"{new_title}\"")
            
            session.commit()
            logger.info(f"✅ Lote de IA completado exitosamente ({success_count}/{len(pending_torrents)}).")
            
        except Exception as e:
            logger.error(f"❌ Error en el procesamiento de IA: {e}")
            for t in pending_torrents:
                t.ai_status = "Error" 
            session.commit()
            if specific_guids:
                raise e


# ==========================================
# FUNCIONES DE TESTEO Y LABORATORIO (UI)
# ==========================================

async def test_single_torrent_ai(guid: str, title: str, description: str, config: AIConfig) -> str:
    """
    Aísla la prueba de un solo torrent. Utilizada exclusivamente por el 
    'Laboratorio de IA' de la web para previsualizar cómo actuaría el prompt actual.
    """
    data_to_send = {guid: {"titulo_actual": title, "sinopsis": description}}
    prompt = _build_prompt(data_to_send)
    
    try:
        result_json = await call_ai_provider(prompt, config)
        parsed_data = _clean_and_parse_json(result_json)
        return parsed_data.get(guid, "Error: La IA no devolvió el ID correcto.")
    except Exception as e:
        raise Exception(f"Fallo en la prueba: {str(e)}")

async def test_ai_connection(config: AIConfig) -> str:
    """
    Envía un prompt de verificación de vida (Ping) al LLM.
    Se asegura de que las credenciales de OpenAI/Gemini o la URL de Ollama funcionen.
    """
    prompt = "Petición de testeo: responde únicamente con la frase exacta 'Estoy escuchando.' sin comillas ni texto adicional."
    try:
        result = await call_ai_provider(prompt, config)
        return result.strip()
    except Exception as e:
        raise Exception(f"Error de conexión: {str(e)}")


# ==========================================
# GESTIÓN DE PROMPTS Y PARSEO DE RESPUESTAS
# ==========================================
import json
import re
from core.logger import logger

def _build_prompt(data_dict: dict) -> str:
    """
    Construye el 'System Prompt' estructurado. Le da el contexto de Sonarr al LLM
    y le pasa los datos utilizando 'Few-Shot Prompting' con ejemplos muy complejos 
    para forzar a la IA a respetar los metadatos técnicos.
    """
    return f"""
    Eres un experto en organizar metadatos de anime para Sonarr.
    Te daré un JSON con uno o varios IDs, sus títulos originales crudos y sus sinopsis.
    Tu tarea es limpiar y formatear cada título para maximizar su compatibilidad.
    
    REGLAS ESTRICTAS (SIGUE ESTE ORDEN):
    1. ETIQUETA DE FANSUB: Mantén SIEMPRE intacto el primer bloque entre corchetes (Ej: [UnionFansub | Soshiki] o [UnionFansub]).
    2. TÍTULO LIMPIO: Extrae el título principal del anime. Elimina palabras basura o comerciales como "Dual", "Censurado", "Sin censura", "Parte 1", "BD", "HD", etc.
    3. TEMPORADA (CRÍTICO): Analiza el título y la sinopsis para deducir la temporada exacta (S01, S02, S03...). Insértala SIEMPRE justo después del título del anime.
       - Si la sinopsis no indica una secuela explícita, usa por defecto S01.
    4. METADATOS TÉCNICOS: Al final del título, debes CONSERVAR EXACTAMENTE y sin alterar ni acortar los bloques técnicos originales de resolución, códecs, audio y subtítulos.
    5. FORMATO DE SALIDA: Devuelve ÚNICAMENTE un diccionario JSON válido sin comillas markdown de bloque (```json). Las claves son los IDs, los valores son los nuevos títulos.
    
    --- EJEMPLOS DE ENTRADA ---
    {{
        "17026": {{
            "titulo_actual": "[UnionFansub | Soshiki] Campione!: Matsurowanu Kamigami to Kamigoroshi no Maou [H.264, 1920x1080 (Blu-ray)] [Audio: Japonés, FLAC, Ingles, FLAC] [Subs: Español (Castellano), Español (Latino), Ingles]",
            "sinopsis": "Un chico de 16 años llamado Kusanagi Godou derrota al dios Verethragna y se convierte en el séptimo Campione..."
        }},
        "4036": {{
            "titulo_actual": "[UnionFansub | Iknime] Yahari Ore no Seishun Love Comedy wa Machigatteiru. (Sin censura) [1080p] [Audio: Japonés] [Subs: Español]",
            "sinopsis": "Tercera temporada de Oregairu. El club de servicio voluntario afronta..."
        }},
        "4069": {{
            "titulo_actual": "[UnionFansub] Boku no Hero Academia (Dual) [H.265, 720p] [Audio: Japonés, Español] [Subs: Español]",
            "sinopsis": "Continuación de la primera temporada de los jóvenes aspirantes a héroes..."
        }}
    }}
    
    --- SALIDA ESPERADA ---
    {{
        "17026": "[UnionFansub | Soshiki] Campione!: Matsurowanu Kamigami to Kamigoroshi no Maou S01 [H.264, 1920x1080 (Blu-ray)] [Audio: Japonés, FLAC, Ingles, FLAC] [Subs: Español (Castellano), Español (Latino), Ingles]",
        "4036": "[UnionFansub | Iknime] Yahari Ore no Seishun Love Comedy wa Machigatteiru. S03 [1080p] [Audio: Japonés] [Subs: Español]",
        "4069": "[UnionFansub] Boku no Hero Academia S01 [H.265, 720p] [Audio: Japonés, Español] [Subs: Español]"
    }}
    
    --- DATOS REALES A PROCESAR ---
    {json.dumps(data_dict, ensure_ascii=False)}
    """

def _clean_and_parse_json(raw_text: str) -> dict:
    """
    Sanitiza la respuesta de la IA. Elimina los bloques markdown de código (```json ... ```) 
    que suelen añadir Gemini y OpenAI, para que el intérprete de Python no crashee al hacer json.loads().
    """
    clean_text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
    clean_text = re.sub(r"```$", "", clean_text.strip()).strip()
    try:
        return json.loads(clean_text)
    except json.JSONDecodeError as e:
        logger.error(f"❌ Error crítico parseando la respuesta de la IA. La respuesta fue: \n{clean_text}")
        raise ValueError("La IA devolvió un formato no válido. Revisa los logs.")


# ==========================================
# COMUNICACIÓN CON APIS EXTERNAS
# ==========================================

async def call_ai_provider(prompt: str, config: AIConfig) -> str:
    """
    Capa de abstracción que maneja el envío HTTP al proveedor seleccionado por el usuario.
    Normaliza la forma de llamar a Gemini, OpenAI y Ollama devolviendo siempre un string puro.
    """
    async with httpx.AsyncClient(timeout=45.0) as client:
        if config.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.model_name}:generateContent?key={config.api_key}"
            resp = await client.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            
        elif config.provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
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