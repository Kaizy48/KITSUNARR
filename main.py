# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import os
import time
import httpx
import secrets
import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, BackgroundTasks, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from dotenv import load_dotenv
from sqlmodel import Session, select
from pydantic import BaseModel

from core.database import engine, create_db_and_tables
from core.models.torrent import TVDBCache
from core.tracker_login import attempt_unionfansub_login
from core.tracker_login import auto_renew_cookie
from services.adapters.union_scraper import search_unionfansub_html, test_unionfansub_connection
from services.adapters.tvdb_scraper import process_pending_tvdb
from services.adapters.tvdb_scraper import clean_for_tvdb, _tvdb_search
from core.models.indexer import IndexerConfig
from core.models.torrent import TorrentCache
from core.models.system import SystemConfig, AIConfig
from core.logger import logger, LOG_FILE
from core.ai_parser import process_pending_torrents, test_single_torrent_ai, test_ai_connection

load_dotenv()

PORT = int(os.getenv("KITSUNARR_PORT", 4080))
HOST = os.getenv("KITSUNARR_HOST", "0.0.0.0")
UNION_COOKIE_ENV = os.getenv("UNIONFANSUB_COOKIE")

templates = Jinja2Templates(directory="templates")


# ==========================================
# TRABAJADORES DE FONDO Y CICLO DE VIDA
# ==========================================

"""
Trabajador de fondo que se ejecuta continuamente de forma asíncrona cada 60 segundos.
Delega el procesamiento de un lote de torrents pendientes al motor de IA,
siempre y cuando la función esté activada en la configuración.
"""
async def ai_background_worker():
    while True:
        await asyncio.sleep(60)
        try:
            await process_pending_torrents()
        except Exception as e:
            logger.error(f"Error en bucle IA: {e}")

"""
Trabajador de fondo que se ejecuta continuamente cada 45 segundos.
Llama al scraper de TheTVDB para buscar y almacenar metadatos candidatos 
de las series recién extraídas antes de que la IA las procese.
"""
async def tvdb_background_worker():
    while True:
        await asyncio.sleep(45) 
        try:
            await process_pending_tvdb()
        except Exception as e:
            logger.error(f"Error en bucle TVDB: {e}")

"""
Maneja el ciclo de vida de la aplicación FastAPI.
Inicializa la base de datos, genera las configuraciones por defecto 
si es la primera ejecución y arranca los demonios de fondo.
"""
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄️ Inicializando Base de Datos SQLite...")
    create_db_and_tables()
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            new_key = secrets.token_hex(16)
            config = SystemConfig(api_key=new_key)
            session.add(config)
            
        ai_config = session.exec(select(AIConfig)).first()
        if not ai_config:
            session.add(AIConfig())
        session.commit()
            
    asyncio.create_task(ai_background_worker())
    asyncio.create_task(tvdb_background_worker())
    logger.info("✅ Base de Datos y Trabajadores de IA y TVDB listos.")
    yield


# ==========================================
# INICIALIZACIÓN DE LA APP FASTAPI
# ==========================================
app = FastAPI(title="Kitsunarr", lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory="static"), name="static")

"""
Capturador de excepciones globales. 
Evita que la aplicación colapse ante errores críticos no previstos,
registrándolos en el log y devolviendo un JSON seguro.
"""
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"❌ Error crítico en el servidor: {exc}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": f"Error interno: {str(exc)}"}
    )

# ==========================================
# RUTAS DE VISTAS (RENDERIZADO HTML - UI)
# ==========================================

"""
Renderiza la vista principal (Dashboard).
"""
@app.get("/")
async def ui_dashboard(request: Request):
    with Session(engine) as session:
        indexers = session.exec(select(IndexerConfig)).all()
    return templates.TemplateResponse(request=request, name="views/indexers.html", context={"indexers": indexers})

"""
Renderiza la vista de Caché de Torrents.
"""
@app.get("/cache")
async def ui_cache_view(request: Request):
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
    return templates.TemplateResponse(request=request, name="views/cache.html", context={"ai_config": ai_config})

"""
Renderiza la vista del Laboratorio de IA.
"""
@app.get("/ai")
async def ui_ai_settings_view(request: Request):
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
    return templates.TemplateResponse(request=request, name="views/ai_settings.html", context={"ai_config": ai_config})

"""
Renderiza la vista de Configuración del sistema.
"""
@app.get("/config")
async def ui_config_view(request: Request):
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        ai_config = session.exec(select(AIConfig)).first()
    
    return templates.TemplateResponse(request=request, name="views/config.html", context={
        "api_key": sys_config.api_key,
        "sys_config": sys_config,
        "ai_config": ai_config
    })

"""
Renderiza la vista de Eventos del Sistema.
"""
@app.get("/eventos")
async def ui_events_view(request: Request):
    return templates.TemplateResponse(request=request, name="views/events.html", context={})

"""
Renderiza la vista de Búsqueda Interactiva.
"""
@app.get("/search")
async def ui_interactive_search_view(request: Request):
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
    return templates.TemplateResponse(request=request, name="views/search.html", context={"ai_config": ai_config})


# ==========================================
# API: PROTOCOLO TORZNAB Y DESCARGAS (INTEGRACIÓN SONARR)
# ==========================================

"""
Endpoint maestro que emula la API Torznab.
Es el punto de entrada para todas las comunicaciones desde Sonarr/Radarr.
Valida la clave API, responde a solicitudes de capacidades (caps) y 
redirige las peticiones de búsqueda al scraper del indexador.
"""
@app.get("/api")
async def torznab_endpoint(request: Request):
    params = request.query_params
    action_type = params.get("t")
    query = params.get("q", "")
    provided_apikey = params.get("apikey", "")
    offset = int(params.get("offset", 0))
    base_url = str(request.base_url).rstrip("/")
    
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        if not sys_config or provided_apikey != sys_config.api_key:
            client_ip = request.client.host if request.client else "Unknown"
            logger.error(f"❌ Acceso denegado: Clave API incorrecta desde {client_ip}")
            error_xml = "<?xml version='1.0' encoding='UTF-8'?><error code='100' description='Invalid API Key'/>"
            return Response(content=error_xml, media_type="application/xml", status_code=401)
    
    if action_type == "caps":
        logger.info("🤝 Sonarr está comprobando nuestras capacidades (t=caps)...")
        caps_xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><caps><server version=\"0.1.0\" title=\"Kitsunarr\" /><limits max=\"100\" default=\"50\" /><retention days=\"500\" /><registration available=\"yes\" open=\"yes\" /><searching><search available=\"yes\" supportedParams=\"q\" /><tv-search available=\"yes\" supportedParams=\"q,season,ep\" /></searching><categories><category id=\"5000\" name=\"TV\"><subcat id=\"5070\" name=\"Anime\" /></category></categories></caps>"
        return Response(content=caps_xml, media_type="application/xml")

    logger.info(f"📡 Sonarr está buscando: '{query}' (Tipo: {action_type}, Offset: {offset})")

    active_cookie = None
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if indexer and indexer.cookie_string and indexer.status == "ok":
            active_cookie = indexer.cookie_string
        elif UNION_COOKIE_ENV and UNION_COOKIE_ENV != "PON_TU_COOKIE_AQUI":
            active_cookie = UNION_COOKIE_ENV

    if not active_cookie:
         logger.error("❌ Sonarr intentó buscar, pero no hay indexador configurado.")
         error_xml = "<?xml version='1.0' encoding='UTF-8'?><error description='Indexador no configurado en Kitsunarr.'/>"
         return Response(content=error_xml, media_type="application/xml", status_code=401)

    logger.info(f"🚀 Buscando '{query}' en UnionFansub...")
    torznab_xml = await search_unionfansub_html(query, active_cookie, base_url, offset)
    return Response(content=torznab_xml, media_type="application/xml")

"""
Ruta proxy interna para la descarga de archivos .torrent reales.
Inyecta la cookie de sesión del indexador para saltar muros de login
y devuelve el archivo binario bittorrent original a Sonarr.
"""
@app.get("/api/download/{guid}")
async def proxy_download_torrent(guid: str):
    clean_guid = guid.replace("_base", "").replace("_ai", "")
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if not indexer or not indexer.cookie_string:
            return Response(content="Indexador no configurado", status_code=401)
        cookie = indexer.cookie_string

    logger.info(f"📥 Sonarr solicita descarga. Original: {guid} | ID Real: {clean_guid}")
    url = f"https://torrent.unionfansub.com/download.php?torrent={clean_guid}"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0 (Kitsunarr; +https://github.com/Kaizy48/KITSUNARR)"
    
    headers = {
        "User-Agent": user_agent, "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": f"https://torrent.unionfansub.com/details.php?id={clean_guid}&hit=1"
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            
            content_type = resp.headers.get("Content-Type", "")
            
            if "text/html" in content_type:
                logger.warning(f"⚠️ El tracker denegó la descarga de {guid} (Devolvió HTML). Intentando recuperar sesión...")
                new_cookie = await auto_renew_cookie()
                
                if new_cookie:
                    headers["Cookie"] = new_cookie
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    content_type = resp.headers.get("Content-Type", "")
                
                if "text/html" in content_type:
                    logger.error("❌ Fallo definitivo: Imposible descargar el torrent tras intento de rescate.")
                    return Response(content="Error: Sesión caducada y sin auto-recuperación.", status_code=502)

            return Response(
                content=resp.content, 
                media_type="application/x-bittorrent",
                headers={"Content-Disposition": f'attachment; filename="{guid}.torrent"'}
            )
    except Exception as e:
        logger.error(f"❌ Error descargando torrent {guid}: {e}")
        return Response(content="Error descargando torrent", status_code=502)


# ==========================================
# API: BÚSQUEDA INTERACTIVA
# ==========================================

"""
Endpoint para realizar una búsqueda manual controlada desde la interfaz web.
Usa el modo 'interactivo' del scraper para devolver una lista de IDs cacheados
en lugar de un XML.
"""
@app.get("/api/ui/search")
async def interactive_search_endpoint(q: str, request: Request):
    logger.info(f"🕵️‍♂️ Realizando búsqueda interactiva con nombre '{q}'")
    
    base_url = str(request.base_url).rstrip("/")
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if not indexer or not indexer.cookie_string:
            return {"success": False, "error": "Indexador no configurado. Falta la cookie."}
        cookie = indexer.cookie_string

    try:
        ids_encontrados = await search_unionfansub_html(q, cookie, base_url, 0, interactivo=True)
    except HTTPException as he:
        return {"success": False, "error": f"Aviso del tracker: {he.detail}"}
    except Exception as e:
        return {"success": False, "error": f"Error interno al buscar: {str(e)}"}
    
    if not ids_encontrados:
        return {"success": True, "results": []}

    with Session(engine) as session:
        statement = select(TorrentCache, TVDBCache.series_name_es, TVDBCache.poster_path).where(
            TorrentCache.guid.in_(ids_encontrados)
        ).join(
            TVDBCache, TorrentCache.tvdb_id == TVDBCache.tvdb_id, isouter=True
        )
        results = session.exec(statement).all()
        
        payload = []
        for torrent, tvdb_name_es, tvdb_poster in results:
            t_dict = jsonable_encoder(torrent)
            t_dict["tvdb_series_name_es"] = tvdb_name_es
            t_dict["tvdb_poster_path"] = tvdb_poster
            payload.append(t_dict)
            
        return {"success": True, "results": payload}


# ==========================================
# API: GESTIÓN DE LA BASE DE DATOS (CACHÉ)
# ==========================================

"""
Obtiene un volcado de los últimos 2000 torrents cacheados en la base de datos local
para popular las tablas de la interfaz de usuario.
"""
@app.get("/api/ui/cache")
async def get_cache_list():
    with Session(engine) as session:
        statement = select(TorrentCache, TVDBCache.series_name_es, TVDBCache.poster_path).join(
            TVDBCache, TorrentCache.tvdb_id == TVDBCache.tvdb_id, isouter=True
        ).order_by(TorrentCache.guid.desc()).limit(2000)
        
        results = session.exec(statement).all()
        
        payload = []
        for torrent, tvdb_name_es, tvdb_poster in results:
            t_dict = jsonable_encoder(torrent)
            t_dict["tvdb_series_name_es"] = tvdb_name_es
            t_dict["tvdb_poster_path"] = tvdb_poster
            payload.append(t_dict)
            
        return {"torrents": payload}

class EditCacheForm(BaseModel):
    ai_translated_title: str
    description: str = ""
    tvdb_id: str = "" 

"""
Recibe las modificaciones hechas por el usuario desde el Modal de Edición Manual.
Sobrescribe el título de IA, la descripción y el ID de TheTVDB, marcando el estado
como 'Manual' o 'Listo' según corresponda.
"""
@app.put("/api/ui/cache/{guid}")
async def edit_cache_entry(guid: str, data: EditCacheForm):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if t:
            t.ai_translated_title = data.ai_translated_title
            t.description = data.description
            t.ai_status = "Manual"
            
            if data.tvdb_id:
                t.tvdb_id = data.tvdb_id
                t.tvdb_status = "Listo"
            else:
                t.tvdb_id = None
                t.tvdb_status = "Candidatos" if t.tvdb_candidates else "Pendiente"
                
            session.commit()
            return {"success": True}
        return {"success": False}

"""
Elimina permanentemente un registro de la tabla de la base de datos local.
"""
@app.delete("/api/ui/cache/{guid}")
async def delete_cache_entry(guid: str):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if t:
            session.delete(t)
            session.commit()
            return {"success": True}
        return {"success": False}

"""
Extrae la tabla completa de torrents cacheados y la devuelve al navegador 
en un archivo JSON descargable a modo de copia de seguridad.
"""
@app.get("/api/ui/cache/export")
async def export_cache_db():
    with Session(engine) as session:
        torrents = session.exec(select(TorrentCache)).all()
        json_data = jsonable_encoder(torrents)
        return Response(
            content=json.dumps(json_data, indent=2), 
            media_type="application/json", 
            headers={"Content-Disposition": "attachment; filename=kitsunarr_cache.json"}
        )

"""
Permite subir un archivo JSON con un volcado previo de torrents e inserta 
todos los registros que no existan ya en la base de datos local.
"""
@app.post("/api/ui/cache/import")
async def import_cache_db(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        imported_count = 0
        
        with Session(engine) as session:
            for item in data:
                existing = session.exec(select(TorrentCache).where(TorrentCache.guid == item.get("guid"))).first()
                if not existing:
                    filtered_item = {k: v for k, v in item.items() if k != "id"}
                    new_t = TorrentCache(**filtered_item)
                    session.add(new_t)
                    imported_count += 1
            session.commit()
            
        logger.info(f"📦 Importación de Caché exitosa: {imported_count} nuevos torrents añadidos.")
        return {"success": True, "count": imported_count}
    except Exception as e:
        logger.error(f"❌ Error importando caché: {e}")
        return {"success": False, "error": str(e)}

# ==========================================
# RUTAS: BIBLIOTECA TVDB (Caché Maestra)
# ==========================================

"""
Renderiza la vista de la Biblioteca Maestra de TheTVDB.
"""
@app.get("/tvdb_cache")
async def ui_tvdb_cache_view(request: Request):
    return templates.TemplateResponse(request=request, name="views/tvdb_cache.html", context={})

"""
Obtiene todas las fichas maestras guardadas en la base de datos local.
"""
@app.get("/api/ui/tvdb_cache")
async def get_tvdb_cache_list():
    with Session(engine) as session:
        tvdb_items = session.exec(select(TVDBCache).order_by(TVDBCache.series_name_es)).all()
        return {"tvdb_cache": jsonable_encoder(tvdb_items)}

"""
Elimina una ficha maestra de la caché. (Útil si el usuario quiere forzar 
que la IA vuelva a buscarla desde cero en el futuro).
"""
@app.delete("/api/ui/tvdb_cache/{tvdb_id}")
async def delete_tvdb_cache_entry(tvdb_id: str):
    with Session(engine) as session:
        t = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id)).first()
        if t:
            session.delete(t)
            session.commit()
            return {"success": True}
        return {"success": False}

# ==========================================
# API: SISTEMA Y SWITCHES AVANZADOS 
# ==========================================

class AdvancedProcessingForm(BaseModel):
    is_enabled: bool
    is_automated: bool

"""
Recibe y guarda en base de datos los estados de encendido/apagado general 
del motor IA y del proceso de fondo de escaneo automático.
"""
@app.post("/api/ui/system/advanced")
async def save_advanced_settings(data: AdvancedProcessingForm):
    with Session(engine) as session:
        conf = session.exec(select(AIConfig)).first()
        if not conf:
            return {"success": False, "error": "Configuración de IA no inicializada."}
            
        conf.is_enabled = data.is_enabled
        conf.is_automated = data.is_automated

        session.add(conf) 
        session.commit()
        
        msg_en = "ACTIVADO" if conf.is_enabled else "DESACTIVADO"
        msg_auto = "ACTIVADA" if conf.is_automated else "DESACTIVADA"
        logger.info(f"⚙️ Funciones Avanzadas -> Procesamiento: {msg_en} | Tarea Automática: {msg_auto}")
        return {"success": True}


# ==========================================
# API: MOTOR DE INTELIGENCIA ARTIFICIAL (TÉCNICO)
# ==========================================

class AIPromptForm(BaseModel):
    custom_prompt: str

"""
Guarda en base de datos el texto del prompt personalizado por el usuario.
"""
@app.post("/api/ui/ai/prompt")
async def save_ai_prompt(data: AIPromptForm):
    with Session(engine) as session:
        conf = session.exec(select(AIConfig)).first()
        if not conf:
            return {"success": False, "error": "Configuración de IA no inicializada."}
        
        conf.custom_prompt = data.custom_prompt
        session.add(conf)
        session.commit()
        return {"success": True}

class AIConfigForm(BaseModel):
    provider: str
    model_name: str
    api_key: str
    base_url: str
    rpm_limit: int = 5
    tpm_limit: int = 250000
    rpd_limit: int = 20

"""
Recibe los detalles de conexión técnicos del LLM (API key, URL, proveedor, límites)
y los almacena de forma persistente.
"""
@app.post("/api/ui/ai/config")
async def save_ai_config(data: AIConfigForm):
    with Session(engine) as session:
        conf = session.exec(select(AIConfig)).first()
        if not conf:
            conf = AIConfig()
            
        conf.provider = data.provider
        conf.model_name = data.model_name
        conf.api_key = data.api_key
        conf.base_url = data.base_url
        
        conf.rpm_limit = data.rpm_limit
        conf.tpm_limit = data.tpm_limit
        conf.rpd_limit = data.rpd_limit
        
        session.add(conf) 
        session.commit()
        
        logger.info("⚙️ Ajustes técnicos y de cuota de IA guardados exitosamente.")
        return {"success": True}

class ForceSpecificAIRequest(BaseModel):
    guids: list[str]

"""
Envía al procesador una lista específica de GUIDs para que sean evaluados 
por la IA de forma inmediata, saltándose la cola del temporizador automático.
"""
@app.post("/api/ui/ai/force_specific")
async def force_ai_process_specific(data: ForceSpecificAIRequest):
    try:
        await process_pending_torrents(specific_guids=data.guids)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

class TestAIRequest(BaseModel):
    guid: str
    config: AIConfigForm

"""
Fuerza una nueva búsqueda de candidatos en TheTVDB para un torrent específico, 
reiniciando su estado por si falló anteriormente.
"""
@app.post("/api/ui/tvdb/force_specific")
async def force_tvdb_process_specific(data: ForceSpecificAIRequest):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key:
            return {"success": False, "error": "TVDB no está configurado."}
            
        for guid in data.guids:
            t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
            if t:
                query = clean_for_tvdb(t.original_title)
                try:
                    results = await asyncio.to_thread(_tvdb_search, config.tvdb_api_key, query)
                    if results:
                        candidates = []
                        for r in results:
                            raw_aliases = r.get("aliases", [])
                            clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
                            candidates.append({
                                "tvdb_id": str(r.get("tvdb_id")), "name": r.get("name"),
                                "aliases": clean_aliases, "image_url": r.get("image_url")
                            })
                        t.tvdb_candidates = json.dumps(candidates, ensure_ascii=False)
                        t.tvdb_status = "Candidatos"
                        t.ai_status = "Pendiente"
                    else:
                        t.tvdb_status = "No Encontrado"
                        t.ai_status = "Manual"
                    session.commit()
                except Exception as e:
                    return {"success": False, "error": str(e)}
                    
        return {"success": True}

"""
Realiza una prueba aislada procesando un único torrent de la caché con los parámetros
temporales proporcionados por la UI. No guarda los cambios en la BD local.
"""
@app.post("/api/ui/ai/test")
async def test_ai_process_endpoint(data: TestAIRequest):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == data.guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado"}
        
        temp_config = AIConfig(
            provider=data.config.provider, model_name=data.config.model_name,
            api_key=data.config.api_key, base_url=data.config.base_url
        )
        
        try:
            logger.info(f"🧠 Probando IA con Torrent \"{t.guid}\" \"{t.enriched_title}\"")
            result = await test_single_torrent_ai(t.guid, t.enriched_title, t.description or "", temp_config)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
        
class PingAIRequest(BaseModel):
    config: AIConfigForm

"""
Lanza un ping de red básico al modelo de lenguaje configurado para medir 
latencias y verificar que las credenciales funcionan.
"""
@app.post("/api/ui/ai/ping")
async def ping_ai_process_endpoint(data: PingAIRequest):
    temp_config = AIConfig(
        provider=data.config.provider, model_name=data.config.model_name,
        api_key=data.config.api_key, base_url=data.config.base_url
    )
    try:
        result = await test_ai_connection(temp_config)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==========================================
# RUTAS DE THETVDB (CONFIGURACIÓN)
# ==========================================

class TVDBConfigForm(BaseModel):
    tvdb_api_key: str
    tvdb_is_enabled: bool = False

"""
Guarda en base de datos la clave API v4 de TheTVDB y el estado de su 
interruptor maestro.
"""
@app.post("/api/ui/system/tvdb")
async def save_tvdb_config(data: TVDBConfigForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if config:
            config.tvdb_api_key = data.tvdb_api_key.strip()
            config.tvdb_is_enabled = data.tvdb_is_enabled
            session.add(config)
            session.commit()
            logger.info("📡 Configuración de TheTVDB guardada localmente.")
            return {"success": True}
        return {"success": False, "error": "No se encontró el bloque de configuración."}

"""
Realiza una petición de login temporal contra TheTVDB para comprobar que 
la clave provista por el usuario es válida.
"""
@app.post("/api/ui/system/tvdb/test")
async def test_tvdb_connection(data: TVDBConfigForm):
    logger.info("🌍 Iniciando test de conexión con la API v4 de TheTVDB...")
    clean_key = data.tvdb_api_key.strip()
    if not clean_key:
        return {"success": False, "error": "La clave API proporcionada está vacía."}
        
    url = "https://api4.thetvdb.com/v4/login"
    payload = {"apikey": clean_key}
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("✅ [TheTVDB] Conexión exitosa. Token de sesión temporal obtenido.")
                return {"success": True}
            elif resp.status_code == 401:
                return {"success": False, "error": "La clave API fue rechazada por TheTVDB (Error 401). Verifica que la copiaste correctamente."}
            else:
                return {"success": False, "error": f"Error del servidor TVDB al procesar la clave (HTTP {resp.status_code})."}
                
    except httpx.RequestError as exc:
        return {"success": False, "error": f"Error de red interno: {str(exc)}"}


# ==========================================
# API: GESTIÓN DE INDEXADORES Y UTILIDADES
# ==========================================

class IndexerForm(BaseModel):
    auth_type: str
    cookie_string: str = ""
    username: str = ""
    password: str = ""

"""
Recibe y procesa el formulario de configuración de un indexador desde la interfaz web.
Si el usuario elige el método 'Auto-Login', intercepta la petición para robar la cookie 
maestra en tiempo real antes de guardar las credenciales en la base de datos local.
Finalmente, ejecuta un test de red para verificar la conectividad de la sesión obtenida.
"""
@app.post("/api/ui/indexer")
async def save_indexer(data: IndexerForm):
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if not indexer:
            indexer = IndexerConfig(name="Union Fansub", identifier="unionfansub", auth_type=data.auth_type)
            session.add(indexer)
            
        if data.auth_type == "login":
            if not data.username or not data.password:
                return {"success": False, "error": "Debes proporcionar usuario y contraseña para el Auto-Login."}

            full_cookie_string = await attempt_unionfansub_login(data.username, data.password)
            
            if full_cookie_string:
                indexer.auth_type = "login"
                indexer.cookie_string = full_cookie_string
                indexer.username = data.username
                indexer.password = data.password
            else:
                return {"success": False, "error": "Credenciales incorrectas o el tracker bloqueó la conexión."}
        else:
            indexer.auth_type = "cookie"
            indexer.cookie_string = data.cookie_string
            indexer.username = None
            indexer.password = None

        is_ok = await test_unionfansub_connection(indexer.cookie_string)
        indexer.status = "ok" if is_ok else "error"
        session.commit()
        
        if not is_ok:
            return {
                "success": False, 
                "error": "No se pudo conectar al tracker. La sesión expiró o la cookie es inválida."
            }
            
        return {"success": True, "status": indexer.status}

"""
Fuerza el testeo manual de un indexador ya configurado enviando cabeceras
completas y devolviendo el resultado de red a la UI.
"""
@app.post("/api/ui/indexer/test/{identifier}")
async def test_existing_indexer(identifier: str):
    logger.info(f"🧪 Iniciando test manual de conexión para el indexador: '{identifier}'...")
    
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if not indexer or not indexer.cookie_string:
            logger.error(f"❌ Test fallido: El indexador '{identifier}' no está configurado o falta la cookie.")
            return {"success": False, "status": "error"}
        
        is_ok = await test_unionfansub_connection(indexer.cookie_string)
        indexer.status = "ok" if is_ok else "error"
        session.commit()
        
        if is_ok:
            logger.info(f"✅ Test exitoso: Conexión establecida y cookie válida para '{identifier}'.")
        else:
            logger.error(f"❌ Test fallido: La cookie de '{identifier}' ha expirado, es inválida o el tracker está caído.")
            
        return {"success": is_ok, "status": indexer.status}

"""
Elimina permanentemente la configuración de un indexador.
"""
@app.delete("/api/ui/indexer/{identifier}")
async def delete_indexer(identifier: str):
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if indexer:
            session.delete(indexer)
            session.commit()
            return {"success": True}
        return {"success": False}

"""
Endpoint intermedio para resolver problemas de CORS y ocultar la IP del usuario.
Recibe la URL de una imagen externa, la descarga usando la IP del servidor backend 
e inyectando cabeceras sigilosas, y devuelve el binario de imagen puro a la UI web.
"""
@app.get("/api/ui/poster")
async def proxy_poster(url: str):
    if not url:
        return Response(status_code=404)
        
    is_tvdb = "thetvdb.com" in url.lower()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
        "Sec-GPC": "1"
    }
    
    if is_tvdb:
        headers["Referer"] = "https://thetvdb.com/"
    else:
        headers["Referer"] = "https://foro.unionfansub.com/"
        with Session(engine) as session:
            indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
            cookie = indexer.cookie_string if indexer and indexer.status == "ok" else ""
            if cookie:
                headers["Cookie"] = cookie

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return Response(content=resp.content, media_type=content_type)
    except Exception as e:
        logger.error(f"❌ Error proxificando la imagen ({url}): {e}")
        return Response(status_code=502)


# ==========================================
# API: SISTEMA Y LOGS
# ==========================================

"""
Lee las últimas 150 líneas del archivo físico de registro (kitsunarr.log)
y se las envía al frontend para mostrarlas en la Consola de Eventos.
"""
@app.get("/api/ui/logs")
async def get_logs():
    if not os.path.exists(LOG_FILE):
        return {"logs": "Aún no hay eventos registrados."}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-150:] 
    return {"logs": "".join(lines)}

"""
Trunca (vacía) completamente el contenido del archivo de logs físico.
"""
@app.delete("/api/ui/logs")
async def clear_logs():
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.truncate(0)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

"""
Genera un nuevo token hexadecimal aleatorio de 16 bytes para usarlo
como clave maestra de Torznab.
"""
@app.post("/api/ui/system/apikey/regenerate")
async def regenerate_apikey():
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        new_key = secrets.token_hex(16)
        config.api_key = new_key
        session.commit()
        return {"success": True, "new_key": new_key}

def force_restart():
    time.sleep(1.5)
    os._exit(0)

"""
Programa la terminación del proceso de Python. Si la aplicación corre bajo Docker,
esto provocará que el contenedor se reinicie automáticamente de forma limpia.
"""
@app.post("/api/ui/system/restart")
async def restart_system(background_tasks: BackgroundTasks):
    background_tasks.add_task(force_restart)
    return {"success": True}

"""
Expone el estado de las integraciones críticas para que el frontend ajuste
las alertas visuales correspondientes.
"""
@app.get("/api/ui/system/status")
async def get_system_status():
    with Session(engine) as session:
        sys = session.exec(select(SystemConfig)).first()
        return {"tvdb_is_enabled": sys.tvdb_is_enabled if sys else False}

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host=HOST, 
        port=PORT, 
        reload=True,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )