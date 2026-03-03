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
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from dotenv import load_dotenv
from sqlmodel import Session, select
from pydantic import BaseModel

from core.database import engine, create_db_and_tables
from services.adapters.union_scraper import search_unionfansub_html, test_unionfansub_connection
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

async def ai_background_worker():
    """
    Trabajador de fondo que se ejecuta cada 60 segundos.
    Se encarga de procesar automáticamente los torrents que están en estado "Pendiente"
    usando el motor de IA configurado, siempre y cuando la opción automática esté activa.
    """
    while True:
        await asyncio.sleep(60)
        try:
            await process_pending_torrents()
        except Exception as e:
            logger.error(f"Error en bucle IA: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Maneja el ciclo de vida de la aplicación FastAPI.
    Se ejecuta al iniciar el servidor: inicializa la base de datos, 
    crea configuraciones por defecto si no existen y arranca los trabajadores de fondo.
    """
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
    logger.info("✅ Base de Datos y Trabajador de IA listos.")
    yield


# ==========================================
# INICIALIZACIÓN DE LA APP FASTAPI
# ==========================================
app = FastAPI(title="Kitsunarr", lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ==========================================
# RUTAS DE VISTAS (RENDERIZADO HTML - UI)
# ==========================================

@app.get("/")
async def ui_dashboard(request: Request):
    """Renderiza la vista principal (Dashboard/Indexadores)."""
    with Session(engine) as session:
        indexers = session.exec(select(IndexerConfig)).all()
    return templates.TemplateResponse(request=request, name="views/indexers.html", context={"indexers": indexers})

@app.get("/cache")
async def ui_cache_view(request: Request):
    """Renderiza la interfaz de gestión de la base de datos local (Caché)."""
    return templates.TemplateResponse(request=request, name="views/cache.html", context={})

@app.get("/ai")
async def ui_ai_settings_view(request: Request):
    """Renderiza la interfaz de Configuración de IA y el Laboratorio de pruebas."""
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
    return templates.TemplateResponse(request=request, name="views/ai_settings.html", context={"ai_config": ai_config})

@app.get("/config")
async def ui_config_view(request: Request):
    """Renderiza la interfaz de Configuración General del Sistema (API Keys)."""
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
    return templates.TemplateResponse(request=request, name="views/config.html", context={"api_key": config.api_key})

@app.get("/eventos")
async def ui_events_view(request: Request):
    """Renderiza la interfaz de la terminal para visualizar los logs en tiempo real."""
    return templates.TemplateResponse(request=request, name="views/events.html", context={})

@app.get("/search")
async def ui_interactive_search_view(request: Request):
    """Renderiza la interfaz de Búsqueda Interactiva manual."""
    return templates.TemplateResponse(request=request, name="views/search.html", context={})


# ==========================================
# API: PROTOCOLO TORZNAB Y DESCARGAS (INTEGRACIÓN SONARR)
# ==========================================

@app.get("/api")
async def torznab_endpoint(request: Request):
    """
    Endpoint principal Torznab compatible con Sonarr/Radarr.
    Gestiona las peticiones de capacidades (caps) y las búsquedas reales en el tracker.
    """
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
        caps_xml = """<?xml version="1.0" encoding="UTF-8"?><caps><server version="0.1.0" title="Kitsunarr" /><limits max="100" default="50" /><retention days="500" /><registration available="yes" open="yes" /><searching><search available="yes" supportedParams="q" /><tv-search available="yes" supportedParams="q,season,ep" /></searching><categories><category id="5000" name="TV"><subcat id="5070" name="Anime" /></category></categories></caps>"""
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

@app.get("/api/download/{guid}")
async def proxy_download_torrent(guid: str):
    """
    Actúa como proxy para descargar el archivo .torrent real desde el tracker.
    Se identifica frente al foro con la cookie de sesión de Kitsunarr para saltar protecciones.
    """
    clean_guid = guid.replace("_base", "").replace("_ai", "")
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if not indexer or not indexer.cookie_string:
            return Response(content="Indexador no configurado", status_code=401)
        cookie = indexer.cookie_string

    logger.info(f"📥 Sonarr solicita descarga. Original: {guid} | ID Real: {clean_guid}")
    url = f"https://torrent.unionfansub.com/download.php?torrent={clean_guid}"
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
    
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
                logger.error(f"❌ Error: Union Fansub denegó la descarga (devolvió HTML).")
                return Response(content="Error: El tracker devolvió HTML en lugar del Torrent.", status_code=502)

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

@app.get("/api/ui/search")
async def interactive_search_endpoint(q: str, request: Request):
    """
    Endpoint utilizado por la UI de Búsqueda Interactiva.
    Llama al scraper en modo 'interactivo' para que popule la caché sin generar XML,
    y devuelve los resultados formateados en JSON para pintar la tabla en la web.
    """
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
        db_results = session.exec(select(TorrentCache).where(TorrentCache.guid.in_(ids_encontrados))).all()
        return {"success": True, "results": jsonable_encoder(db_results)}


# ==========================================
# API: GESTIÓN DE LA BASE DE DATOS (CACHÉ)
# ==========================================

@app.get("/api/ui/cache")
async def get_cache_list():
    """Devuelve la lista de los últimos 2000 torrents cacheados para la vista web."""
    with Session(engine) as session:
        torrents = session.exec(select(TorrentCache).order_by(TorrentCache.id.desc()).limit(2000)).all()
        return {"torrents": jsonable_encoder(torrents)}

class EditCacheForm(BaseModel):
    ai_translated_title: str
    description: str = ""

@app.put("/api/ui/cache/{guid}")
async def edit_cache_entry(guid: str, data: EditCacheForm):
    """Actualiza manualmente el título traducido y la sinopsis de un torrent específico."""
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if t:
            t.ai_translated_title = data.ai_translated_title
            t.description = data.description
            t.ai_status = "Manual"
            session.commit()
            return {"success": True}
        return {"success": False}

@app.delete("/api/ui/cache/{guid}")
async def delete_cache_entry(guid: str):
    """Elimina permanentemente una entrada de la caché de torrents."""
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if t:
            session.delete(t)
            session.commit()
            return {"success": True}
        return {"success": False}

@app.get("/api/ui/cache/export")
async def export_cache_db():
    """Exporta toda la tabla de caché a un archivo descargable JSON."""
    with Session(engine) as session:
        torrents = session.exec(select(TorrentCache)).all()
        json_data = jsonable_encoder(torrents)
        return Response(
            content=json.dumps(json_data, indent=2), 
            media_type="application/json", 
            headers={"Content-Disposition": "attachment; filename=kitsunarr_cache.json"}
        )

@app.post("/api/ui/cache/import")
async def import_cache_db(file: UploadFile = File(...)):
    """Importa un archivo JSON para fusionarlo con la caché existente evitando duplicados."""
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
# API: MOTOR DE INTELIGENCIA ARTIFICIAL
# ==========================================

class AIConfigForm(BaseModel):
    is_enabled: bool
    is_automated: bool
    provider: str
    model_name: str
    api_key: str
    base_url: str

@app.post("/api/ui/ai/config")
async def save_ai_config(data: AIConfigForm):
    """Guarda en la base de datos la configuración principal del proveedor de IA."""
    with Session(engine) as session:
        conf = session.exec(select(AIConfig)).first()
        if not conf:
            conf = AIConfig()
            session.add(conf)
            
        conf.is_enabled = data.is_enabled
        conf.is_automated = data.is_automated
        conf.provider = data.provider
        conf.model_name = data.model_name
        conf.api_key = data.api_key
        conf.base_url = data.base_url
        session.commit()
        
        msg_en = "ACTIVADO" if conf.is_enabled else "DESACTIVADO"
        msg_auto = "ACTIVADA" if conf.is_automated else "DESACTIVADA"
        logger.info(f"⚙️ Ajustes de IA Modificados -> Motor General: {msg_en} | Tarea Automática: {msg_auto}")
        return {"success": True}

class ForceSpecificAIRequest(BaseModel):
    guids: list[str]

@app.post("/api/ui/ai/force_specific")
async def force_ai_process_specific(data: ForceSpecificAIRequest):
    """Fuerza el procesamiento síncrono de IA para un lote específico de GUIDs desde la UI."""
    try:
        await process_pending_torrents(specific_guids=data.guids)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

class TestAIRequest(BaseModel):
    guid: str
    config: AIConfigForm

@app.post("/api/ui/ai/test")
async def test_ai_process_endpoint(data: TestAIRequest):
    """Petición individual para probar el prompt de IA en el 'Laboratorio' de la interfaz web."""
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == data.guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado"}
        
        temp_config = AIConfig(
            provider=data.config.provider, model_name=data.config.model_name,
            api_key=data.config.api_key, base_url=data.config.base_url
        )
        
        try:
            logger.info(f"🧠 Enviando petición para Título Enriquecido con IA de Torrent \"{t.guid}\" \"{t.enriched_title}\"")
            result = await test_single_torrent_ai(t.guid, t.enriched_title, t.description or "", temp_config)
            logger.info(f"🧠 Recibida petición para Título Enriquecido con IA de Torrent \"{t.guid}\" \"{result}\"")
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
        
class PingAIRequest(BaseModel):
    config: AIConfigForm

@app.post("/api/ui/ai/ping")
async def ping_ai_process_endpoint(data: PingAIRequest):
    """Verifica que las credenciales de la IA sean correctas realizando un ping de prueba simple."""
    temp_config = AIConfig(
        provider=data.config.provider, model_name=data.config.model_name,
        api_key=data.config.api_key, base_url=data.config.base_url
    )
    try:
        logger.info(f"📡 Enviando Ping de diagnóstico al proveedor IA '{temp_config.provider}'...")
        result = await test_ai_connection(temp_config)
        logger.info(f"✅ Ping recibido del proveedor IA '{temp_config.provider}': '{result}'")
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==========================================
# API: GESTIÓN DE INDEXADORES Y UTILIDADES
# ==========================================

class IndexerForm(BaseModel):
    auth_type: str
    cookie_string: str = ""
    username: str = ""
    password: str = ""

@app.post("/api/ui/indexer")
async def save_indexer(data: IndexerForm):
    """Guarda la configuración de autenticación de un indexador (tracker) y prueba su conexión."""
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if not indexer:
            indexer = IndexerConfig(name="Union Fansub", identifier="unionfansub", auth_type=data.auth_type)
            session.add(indexer)
        indexer.auth_type = data.auth_type
        indexer.cookie_string = data.cookie_string
        indexer.username = data.username
        indexer.password = data.password
        logger.info("🌍 Probando conexión con Union Fansub...")
        is_ok = await test_unionfansub_connection(data.cookie_string)
        indexer.status = "ok" if is_ok else "error"
        session.commit()
        return {"success": True, "status": indexer.status}

@app.post("/api/ui/indexer/test/{identifier}")
async def test_existing_indexer(identifier: str):
    """Vuelve a probar la conexión de un indexador guardado para verificar si la cookie expiró."""
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if not indexer or not indexer.cookie_string:
            return {"success": False, "status": "error"}
        logger.info(f"🌍 Testeando conexión manualmente a {indexer.name}...")
        is_ok = await test_unionfansub_connection(indexer.cookie_string)
        indexer.status = "ok" if is_ok else "error"
        session.commit()
        return {"success": is_ok, "status": indexer.status}

@app.delete("/api/ui/indexer/{identifier}")
async def delete_indexer(identifier: str):
    """Elimina la configuración de un indexador de la base de datos."""
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if indexer:
            session.delete(indexer)
            session.commit()
            logger.info(f"🗑️ Indexador eliminado de la base de datos: {indexer.name}")
            return {"success": True}
        return {"success": False}

@app.get("/api/ui/poster")
async def proxy_poster(url: str):
    """
    Proxy de Imágenes interno para pedir la imagen usando la cookie de Kitsunarr y servirla
    en memoria a la interfaz web.
    """
    if not url:
        return Response(status_code=404)
        
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        cookie = indexer.cookie_string if indexer and indexer.status == "ok" else ""
        
    if not cookie and UNION_COOKIE_ENV and UNION_COOKIE_ENV != "PON_TU_COOKIE_AQUI":
        cookie = UNION_COOKIE_ENV

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
        "Referer": "https://torrent.unionfansub.com/"
    }
    if cookie: headers["Cookie"] = cookie

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

@app.get("/api/ui/logs")
async def get_logs():
    """Devuelve las últimas 150 líneas del archivo de registro (log) interno de Kitsunarr."""
    if not os.path.exists(LOG_FILE):
        return {"logs": "Aún no hay eventos registrados."}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-150:] 
    return {"logs": "".join(lines)}

@app.delete("/api/ui/logs")
async def clear_logs():
    """Vacía por completo el archivo de registro interno de Kitsunarr."""
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.truncate(0)
        logger.info("🧹 Registro de eventos limpiado por el usuario.")
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/ui/system/apikey/regenerate")
async def regenerate_apikey():
    """Genera una nueva API Key de seguridad, invalidando la conexión actual con Sonarr."""
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        new_key = secrets.token_hex(16)
        config.api_key = new_key
        session.commit()
        logger.info("🔄 Clave API regenerada por el usuario.")
        return {"success": True, "new_key": new_key}

def force_restart():
    time.sleep(1.5)
    logger.warning("🛑 Reiniciando servidor por petición del usuario...")
    os._exit(0)

@app.post("/api/ui/system/restart")
async def restart_system(background_tasks: BackgroundTasks):
    """Mata el proceso de Python para forzar un reinicio del contenedor Docker."""
    background_tasks.add_task(force_restart)
    return {"success": True}


if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host=HOST, 
        port=PORT, 
        reload=True,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )