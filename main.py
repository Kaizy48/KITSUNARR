# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import os
import time
import secrets
import json
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
import jwt
from fastapi.responses import RedirectResponse

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response, BackgroundTasks, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from dotenv import load_dotenv
from sqlmodel import Session, select, delete
from pydantic import BaseModel

import core.ai_parser as ai_parser
from core.ai_parser import process_pending_torrents, test_single_torrent_ai, test_ai_connection
from core.database import engine, create_db_and_tables
from core.logger import logger, LOG_FILE
from core.models.indexer import IndexerConfig
from core.models.system import SystemConfig, AIConfig
from core.models.torrent import TorrentCache, TorrentTVDBCandidates, TVDBCache, TVDBEpisodes
from core.tracker_login import attempt_unionfansub_login, auto_renew_cookie
from services.adapters.union_scraper import search_unionfansub_html, test_unionfansub_connection
from services.adapters.tvdb_scraper import process_pending_tvdb, clean_for_tvdb, _tvdb_search, fetch_tvdb_episodes, fetch_full_tvdb_series
from services.export import export_torrents_only, export_tvdb_only, export_full_bundle, import_relational_data
from services.encrypt import SECRETS_FILE, encrypt_secret, decrypt_secret
from services.arr_manager import sync_indexer_to_arr

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
Inicializa la base de datos, carga o genera las llaves de cifrado, 
verifica la existencia del administrador y arranca los demonios.
"""
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄️ Inicializando Base de Datos SQLite...")
    create_db_and_tables()
    
    if not os.path.exists(SECRETS_FILE):
        logger.info("🔑 No se ha detectado clave de cifrado. Se va a proceder a generar una nueva llave maestra en 'secrets.xml'...")
    else:
        logger.info("🔑 Clave de cifrado maestra detectada y cargada exitosamente.")
    
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            logger.info("⚙️ Configuración del sistema no encontrada. Generando configuración inicial y Torznab API Key...")
            new_key = secrets.token_hex(16)
            config = SystemConfig(api_key=new_key)
            session.add(config)
            session.commit()
            
        ai_config = session.exec(select(AIConfig)).first()
        if not ai_config:
            logger.info("🧠 Configuración de IA no encontrada. Generando perfil por defecto...")
            session.add(AIConfig())
            session.commit()
            
        if not config.admin_password_hash:
            logger.warning("⚠️ ATENCIÓN: No hay usuario administrador configurado en la base de datos.")
            logger.warning("⚠️ La aplicación entrará en modo 'Setup' en el primer acceso web.")
        else:
            logger.info(f"🛡️ Sistema securizado: Administrador '{config.admin_user}' verificado.")
            
    logger.info("🚀 Arrancando trabajadores de fondo (Workers)...")
    asyncio.create_task(ai_background_worker())
    asyncio.create_task(tvdb_background_worker())
    logger.info("✅ Kitsunarr Core iniciado correctamente. Listo para recibir peticiones.")
    yield


# ==========================================
# INICIALIZACIÓN DE LA APP FASTAPI
# ==========================================

app = FastAPI(title="Kitsunarr", lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==========================================
# MIDDLEWARE DE SEGURIDAD (GUARDIÁN)
# ==========================================
from services.encrypt import MASTER_KEY

"""
Guardián global que intercepta el 100% de las peticiones entrantes.
Aplica doble política de seguridad:
1. Rutas Sonarr/Radarr: Exige la Torznab API Key en la URL.
2. Rutas UI Web: Exige una cookie JWT válida. Redirige a login/setup si no la hay.
"""
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    
    public_paths = ["/login", "/setup", "/api/ui/auth/login", "/api/ui/auth/setup", "/api/ui/system/restart"]
    if path.startswith("/static") or path in public_paths:
        return await call_next(request)
        
    if path == "/api" or path.startswith("/api/download"):
        provided_apikey = request.query_params.get("apikey", "")
        with Session(engine) as session:
            config = session.exec(select(SystemConfig)).first()
            if not config or provided_apikey != config.api_key:
                client_ip = request.client.host if request.client else "Unknown"
                logger.warning(f"❌ Acceso denegado a {path} desde {client_ip} (API Key inválida: '{provided_apikey}')")
                return Response(content="<?xml version='1.0' encoding='UTF-8'?><error code='100' description='Invalid API Key'/>", media_type="application/xml", status_code=401)
        return await call_next(request)

    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        admin_exists = bool(config and config.admin_password_hash)

    token = request.cookies.get("kitsunarr_session")
    is_authenticated = False
    
    if token:
        try:
            jwt.decode(token, MASTER_KEY, algorithms=["HS256"])
            is_authenticated = True
        except Exception:
            pass
            
    if not is_authenticated:
        if not admin_exists:
            if path.startswith("/api/ui"):
                return JSONResponse(status_code=401, content={"success": False, "redirect": "/setup"})
            return RedirectResponse(url="/setup")
        else:
            if path.startswith("/api/ui"):
                return JSONResponse(status_code=401, content={"success": False, "redirect": "/login"})
            return RedirectResponse(url="/login")
            
    return await call_next(request)

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
# RUTAS DE AUTENTICACIÓN (LOGIN Y SETUP)
# ==========================================
from datetime import timedelta
from services.encrypt import hash_password, verify_password

class SetupForm(BaseModel):
    username: str
    password: str

@app.post("/api/ui/auth/setup")
async def setup_admin(data: SetupForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if config.admin_password_hash:
            return {"success": False, "error": "El administrador ya está configurado. Ve al login."}
            
        config.admin_user = data.username
        config.admin_password_hash = hash_password(data.password)
        session.add(config)
        session.commit()
        
        token = jwt.encode({"user": data.username, "exp": datetime.utcnow() + timedelta(days=7)}, MASTER_KEY, algorithm="HS256")
        response = JSONResponse(content={"success": True})
        response.set_cookie(key="kitsunarr_session", value=token, httponly=True, max_age=604800)
        return response

class LoginForm(BaseModel):
    username: str
    password: str

@app.post("/api/ui/auth/login")
async def login_admin(data: LoginForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        
        if not config or config.admin_user != data.username or not verify_password(data.password, config.admin_password_hash):
            return {"success": False, "error": "Usuario o contraseña incorrectos."}
            
        token = jwt.encode({"user": data.username, "exp": datetime.utcnow() + timedelta(days=7)}, MASTER_KEY, algorithm="HS256")
        response = JSONResponse(content={"success": True})
        response.set_cookie(key="kitsunarr_session", value=token, httponly=True, max_age=604800)
        return response

@app.post("/api/ui/auth/logout")
async def logout_admin():
    response = JSONResponse(content={"success": True})
    response.delete_cookie("kitsunarr_session")
    return response

@app.get("/login")
async def ui_login_view(request: Request):
    return templates.TemplateResponse(request=request, name="views/login.html", context={})

@app.get("/setup")
async def ui_setup_view(request: Request):
    return templates.TemplateResponse(request=request, name="views/setup.html", context={})

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
        
    if ai_config and ai_config.api_key:
        ai_config.api_key = "********"
        
    return templates.TemplateResponse(request=request, name="views/ai_settings.html", context={"ai_config": ai_config})

"""
Renderiza la vista de Configuración del sistema.
"""
@app.get("/config")
async def ui_config_view(request: Request):
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        ai_config = session.exec(select(AIConfig)).first()
        
    if sys_config:
        if sys_config.tvdb_api_key: sys_config.tvdb_api_key = "********"
        if sys_config.sonarr_key: sys_config.sonarr_key = "********"
        if sys_config.radarr_key: sys_config.radarr_key = "********"
        
    if ai_config and ai_config.api_key:
        ai_config.api_key = "********"
    
    return templates.TemplateResponse(request=request, name="views/config.html", context={
        "api_key": sys_config.api_key if sys_config else "",
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
Renderiza la vista de Búsqueda Interactiva en el Tracker.
"""
@app.get("/search")
async def ui_interactive_search_view(request: Request):
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
    return templates.TemplateResponse(request=request, name="views/search.html", context={"ai_config": ai_config})

"""
Renderiza la vista de la Caché Maestra de TheTVDB.
"""
@app.get("/tvdb_cache")
async def ui_tvdb_cache_view(request: Request):
    return templates.TemplateResponse(request=request, name="views/tvdb_cache.html", context={})

"""
Renderiza la vista de Búsqueda Interactiva en TheTVDB.
"""
@app.get("/tvdb_search")
async def ui_tvdb_search_view(request: Request):
    return templates.TemplateResponse(request=request, name="views/tvdb_search.html", context={})


# ==========================================
# API: PROTOCOLO TORZNAB Y DESCARGAS (INTEGRACIÓN SONARR)
# ==========================================

"""
Endpoint maestro que emula la API Torznab.
Es el punto de entrada para todas las comunicaciones desde Sonarr/Radarr.
Valida la clave API mediante el middleware, responde a solicitudes de capacidades (caps) y 
redirige las peticiones de búsqueda al scraper del indexador inyectando la clave para descargas.
"""
@app.get("/api")
async def torznab_endpoint(request: Request):
    params = request.query_params
    action_type = params.get("t")
    query = params.get("q", "")
    offset = int(params.get("offset", 0))
    base_url = str(request.base_url).rstrip("/")
    
    if action_type == "caps":
        logger.info("🤝 Sonarr está comprobando nuestras capacidades (t=caps)...")
        caps_xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><caps><server version=\"0.1.0\" title=\"Kitsunarr\" /><limits max=\"100\" default=\"50\" /><retention days=\"500\" /><registration available=\"yes\" open=\"yes\" /><searching><search available=\"yes\" supportedParams=\"q\" /><tv-search available=\"yes\" supportedParams=\"q,season,ep\" /></searching><categories><category id=\"5000\" name=\"TV\"><subcat id=\"5070\" name=\"Anime\" /></category></categories></caps>"
        return Response(content=caps_xml, media_type="application/xml")

    logger.info(f"📡 Sonarr está buscando: '{query}' (Tipo: {action_type}, Offset: {offset})")

    active_cookie = None
    system_api_key = ""
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        if sys_config:
            system_api_key = sys_config.api_key
            
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        if indexer and indexer.cookie_string and indexer.status == "ok":
            from services.encrypt import decrypt_secret
            active_cookie = decrypt_secret(indexer.cookie_string)
        elif UNION_COOKIE_ENV and UNION_COOKIE_ENV != "PON_TU_COOKIE_AQUI":
            active_cookie = UNION_COOKIE_ENV

    if not active_cookie:
         logger.error("❌ Sonarr intentó buscar, pero no hay indexador configurado.")
         error_xml = "<?xml version='1.0' encoding='UTF-8'?><error description='Indexador no configurado en Kitsunarr.'/>"
         return Response(content=error_xml, media_type="application/xml", status_code=401)

    logger.info(f"🚀 Buscando '{query}' en UnionFansub...")
    torznab_xml = await search_unionfansub_html(query, active_cookie, base_url, system_api_key, offset)
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
        cookie = decrypt_secret(indexer.cookie_string)

    logger.info(f"📥 Sonarr solicita descarga. Original: {guid} | ID Real: {clean_guid}")
    url = f"https://torrent.unionfansub.com/download.php?torrent={clean_guid}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0 (Kitsunarr; +https://github.com/Kaizy48/KITSUNARR)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Host": "torrent.unionfansub.com",
        "Referer": f"https://torrent.unionfansub.com/details.php?id={clean_guid}&hit=1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cookie": cookie
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
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
        cookie = decrypt_secret(indexer.cookie_string)

    try:
        ids_encontrados = await search_unionfansub_html(q, cookie, base_url, "", 0, interactivo=True)
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
Helper asíncrono para descargar la ficha maestra y los episodios en segundo plano
cuando el usuario asigna un ID de TheTVDB manualmente.
"""
async def background_fetch_tvdb(tvdb_id: str):
    config_copy = None
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if config and config.tvdb_api_key:
            config_copy = SystemConfig(tvdb_api_key=config.tvdb_api_key)
            
    if config_copy:
        logger.info(f"⚙️ [Background] Construyendo Ficha Maestra y Episodios para ID manual: {tvdb_id}")
        await fetch_full_tvdb_series(tvdb_id, config_copy)
        await fetch_tvdb_episodes(tvdb_id, config_copy)

"""
Recibe las modificaciones hechas por el usuario desde el Modal de Edición Manual.
Sobrescribe el título de IA, la descripción y el ID de TheTVDB.
Si se proporciona un ID nuevo, dispara la descarga de metadatos en segundo plano.
"""
@app.put("/api/ui/cache/{guid}")
async def edit_cache_entry(guid: str, data: EditCacheForm, background_tasks: BackgroundTasks):
    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if t:
            t.ai_translated_title = data.ai_translated_title
            t.description = data.description
            t.ai_status = "Manual"
            
            if data.tvdb_id:
                session.exec(delete(TorrentTVDBCandidates).where(TorrentTVDBCandidates.torrent_guid == guid))
                
                t.tvdb_id = data.tvdb_id
                t.tvdb_status = "Listo"
                
                background_tasks.add_task(background_fetch_tvdb, data.tvdb_id)
            else:
                t.tvdb_id = None
                t.tvdb_status = "Pendiente"
                
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
Extrae la base de conocimientos estructurada según el módulo solicitado.
Puede exportar solo torrents, solo metadatos TVDB, o un Bundle relacional maestro.
"""
@app.get("/api/ui/cache/export")
async def export_cache_db(module: str = "bundle"):
    with Session(engine) as session:
        if module == "torrents":
            json_data = export_torrents_only(session)
            filename = "kitsunarr_torrents.json"
        elif module == "tvdb":
            json_data = export_tvdb_only(session)
            filename = "kitsunarr_tvdb.json"
        else:  # bundle
            json_data = export_full_bundle(session)
            filename = "kitsunarr_bundle.json"
            
        return Response(
            content=json.dumps(json_data, indent=2), 
            media_type="application/json", 
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

"""
Permite subir un archivo JSON (Bundle parcial o total) e inserta 
los registros de forma segura validando llaves foráneas.
Dispara descargas de TheTVDB en segundo plano para IDs huérfanos.
"""
@app.post("/api/ui/cache/import")
async def import_cache_db(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        base_url = str(request.base_url).rstrip("/")
        
        with Session(engine) as session:
            result = await import_relational_data(data, base_url)
            
        missing_ids = result.get("missing_tvdb_ids", [])
        for tvdb_id in missing_ids:
            background_tasks.add_task(background_fetch_tvdb, tvdb_id)
            
        counts = result.get("counts", {})
        total_imported = sum(counts.values())
        
        if missing_ids:
            logger.warning(f"⚠️ Importación parcial: {len(missing_ids)} torrents apuntan a series desconocidas. Iniciando descarga TVDB en segundo plano...")
            
        return {"success": True, "counts": counts, "missing_count": len(missing_ids), "total": total_imported}
        
    except Exception as e:
        logger.error(f"❌ Error importando caché: {e}")
        return {"success": False, "error": str(e)}


# ==========================================
# RUTAS: BIBLIOTECA Y BÚSQUEDA THETVDB
# ==========================================

"""
Obtiene TODAS las fichas de TVDB guardadas, pero SOLO devuelve al frontend
las que son 'Fichas Maestras' (is_full_record = True).
"""
@app.get("/api/ui/tvdb_cache")
async def get_tvdb_cache_list():
    with Session(engine) as session:
        tvdb_items = session.exec(select(TVDBCache).where(TVDBCache.is_full_record == True).order_by(TVDBCache.series_name_es)).all()
        return {"tvdb_cache": jsonable_encoder(tvdb_items)}

"""
Devuelve todos los episodios asociados a una serie específica de la caché local.
"""
@app.get("/api/ui/tvdb_cache/{tvdb_id}/episodes")
async def get_tvdb_episodes(tvdb_id: str):
    with Session(engine) as session:
        eps = session.exec(select(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == tvdb_id).order_by(TVDBEpisodes.season_number, TVDBEpisodes.episode_number)).all()
        return {"success": True, "episodes": jsonable_encoder(eps)}

"""
Elimina permanentemente una Ficha Maestra de TheTVDB y sus episodios asociados de la base local.
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

"""
Devuelve una lista de TODAS las series conocidas (Maestras y Candidatos)
para alimentar el buscador 'Omnibox' del modal de edición de la interfaz.
"""
@app.get("/api/ui/tvdb/local_candidates")
async def get_local_candidates():
    with Session(engine) as session:
        items = session.exec(select(TVDBCache)).all()
        return {"success": True, "results": jsonable_encoder(items)}
    
"""
Devuelve únicamente los candidatos de TheTVDB que están estrictamente 
vinculados a un Torrent específico mediante la tabla relacional.
"""
@app.get("/api/ui/torrent/{guid}/candidates")
async def get_torrent_specific_candidates(guid: str):
    with Session(engine) as session:
        statement = select(TVDBCache).join(
            TorrentTVDBCandidates, TVDBCache.tvdb_id == TorrentTVDBCandidates.tvdb_id
        ).where(
            TorrentTVDBCandidates.torrent_guid == guid
        )
        results = session.exec(statement).all()
        return {"success": True, "results": jsonable_encoder(results)}

"""
Realiza una búsqueda viva en la API externa de TheTVDB (Búsqueda Interactiva).
"""
@app.get("/api/ui/tvdb/remote_search")
async def remote_tvdb_search(q: str):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key:
            return {"success": False, "error": "TheTVDB no está configurado."}
        
        try:
            decrypted_key = decrypt_secret(config.tvdb_api_key)
            results = await asyncio.to_thread(_tvdb_search, decrypted_key, q)
            return {"success": True, "results": results}
        except Exception as e:
            return {"success": False, "error": str(e)}

"""
Convierte un candidato encontrado en el buscador remoto en una Ficha Maestra
permanente, descargando sus temporadas y episodios y actualizando la BD.
"""
@app.post("/api/ui/tvdb/fetch_master/{tvdb_id}")
async def force_fetch_master(tvdb_id: str):
    config_copy = None
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if config and config.tvdb_api_key:
            config_copy = SystemConfig(tvdb_api_key=config.tvdb_api_key)
            
    if not config_copy:
        return {"success": False, "error": "TVDB no configurado."}
        
    try:
        await fetch_full_tvdb_series(tvdb_id, config_copy)
        await fetch_tvdb_episodes(tvdb_id, config_copy)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==========================================
# API: MOTOR DE INTELIGENCIA ARTIFICIAL (TÉCNICO)
# ==========================================

class AIAdvancedForm(BaseModel):
    is_enabled: bool
    is_automated: bool

"""
Guarda los ajustes avanzados del motor de IA (Activación global y automatización).
"""
@app.post("/api/ui/system/advanced")
async def save_advanced_ai_settings(data: AIAdvancedForm):
    with Session(engine) as session:
        conf = session.exec(select(AIConfig)).first()
        if not conf:
            return {"success": False, "error": "Configuración de IA no inicializada."}
            
        conf.is_enabled = data.is_enabled
        conf.is_automated = data.is_automated
        
        session.add(conf)
        session.commit()
        
        estado = "Activada" if data.is_enabled else "Desactivada"
        auto = "ON" if data.is_automated else "OFF"
        logger.info(f"⚙️ Ajustes IA actualizados -> Motor: {estado} | Worker Automático: {auto}")
        
        return {"success": True}

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
y los almacena de forma persistente cifrando la clave API si ha cambiado.
"""
@app.post("/api/ui/ai/config")
async def save_ai_config(data: AIConfigForm):
    with Session(engine) as session:
        conf = session.exec(select(AIConfig)).first()
        if not conf:
            conf = AIConfig()
            
        conf.provider = data.provider
        conf.model_name = data.model_name
        if data.api_key != "********":
            conf.api_key = encrypt_secret(data.api_key)
        conf.base_url = data.base_url
        
        conf.rpm_limit = data.rpm_limit
        conf.tpm_limit = data.tpm_limit
        conf.rpd_limit = data.rpd_limit
        
        session.add(conf) 
        session.commit()
        
        ai_parser._ai_sleep_until = None
        ai_parser._ram_daily_count = 0
        ai_parser._ram_last_date = datetime.utcnow().date()
        
        logger.info("⚙️ Ajustes técnicos guardados. Worker despertado y contadores a 0.")
        return {"success": True}

"""
NUEVO: Botón maestro para poner a 0 el contador diario y 
despertar a la IA si estaba bloqueada por un error del proveedor.
"""
@app.post("/api/ui/ai/reset_quota")
async def api_reset_ai_quota():
    ai_parser._ai_sleep_until = None
    ai_parser._ram_daily_count = 0
    ai_parser._ram_last_date = datetime.utcnow().date()
    
    logger.info("🔄 Cuota de IA y bloqueos reiniciados manualmente en RAM.")
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

"""
Fuerza una nueva búsqueda de candidatos en TheTVDB para un torrent específico, 
reiniciando su estado por si falló anteriormente. (Adaptado a Arquitectura Relacional)
"""
@app.post("/api/ui/tvdb/force_specific")
async def force_tvdb_process_specific(data: ForceSpecificAIRequest):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config or not config.tvdb_api_key:
            return {"success": False, "error": "TVDB no está configurado."}
            
        decrypted_key = decrypt_secret(config.tvdb_api_key)
            
        for guid in data.guids:
            t = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
            if t:
                query = clean_for_tvdb(t.original_title)
                logger.info(f"🔄 [Manual] Forzando re-escaneo en TVDB para '{query}' (Torrent: {guid})")
                
                try:
                    results = await asyncio.to_thread(_tvdb_search, decrypted_key, query)
                    
                    session.exec(delete(TorrentTVDBCandidates).where(TorrentTVDBCandidates.torrent_guid == guid))
                    
                    if results:
                        logger.info(f"✅ [Manual] Encontrados {len(results)} candidatos para '{query}'. Actualizando base local...")
                        for r in results:
                            tvdb_id_str = str(r.get("tvdb_id"))
                            existing_show = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id_str)).first()
                            
                            if not existing_show:
                                raw_aliases = r.get("aliases", [])
                                clean_aliases = [a.get("name") if isinstance(a, dict) else a for a in raw_aliases] if raw_aliases else []
                                new_show = TVDBCache(
                                    tvdb_id=tvdb_id_str, series_name_es=r.get("name", "Desconocido"),
                                    aliases=json.dumps(clean_aliases, ensure_ascii=False),
                                    overview_basic=r.get("overview", "Sin sinopsis"),
                                    poster_path=r.get("image_url"), first_aired=r.get("year", "Desconocido"),
                                    status=r.get("status", "Desconocido"), is_full_record=False
                                )
                                session.add(new_show)
                                session.commit()
                            
                            new_link = TorrentTVDBCandidates(torrent_guid=guid, tvdb_id=tvdb_id_str)
                            session.add(new_link)
                            
                        t.tvdb_status = "Candidatos"
                        t.ai_status = "Pendiente"
                    else:
                        logger.warning(f"⚠️ [Manual] TheTVDB no devolvió resultados para '{query}'.")
                        t.tvdb_status = "No Encontrado"
                        t.ai_status = "Manual"
                    
                    session.commit()
                except Exception as e:
                    logger.error(f"❌ [Manual] Error forzando escaneo TVDB para '{query}': {str(e)}")
                    return {"success": False, "error": str(e)}
                    
        return {"success": True}

class TestAIRequest(BaseModel):
    guid: str
    config: AIConfigForm

"""
Realiza una prueba aislada procesando un único torrent de la caché.
"""
@app.post("/api/ui/ai/test")
async def test_ai_process_endpoint(data: TestAIRequest):
    api_key_to_use = data.config.api_key
    if api_key_to_use == "********":
        with Session(engine) as session:
            conf = session.exec(select(AIConfig)).first()
            api_key_to_use = conf.api_key if conf else ""
    else:
        api_key_to_use = encrypt_secret(api_key_to_use) if api_key_to_use else ""

    with Session(engine) as session:
        t = session.exec(select(TorrentCache).where(TorrentCache.guid == data.guid)).first()
        if not t:
            return {"success": False, "error": "Torrent no encontrado"}
            
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
        
    temp_config = AIConfig(
        provider=data.config.provider, model_name=data.config.model_name,
        api_key=api_key_to_use, base_url=data.config.base_url
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
Lanza un ping de red básico al modelo de lenguaje.
"""
@app.post("/api/ui/ai/ping")
async def ping_ai_process_endpoint(data: PingAIRequest):
    api_key_to_use = data.config.api_key
    if api_key_to_use == "********":
        with Session(engine) as session:
            conf = session.exec(select(AIConfig)).first()
            api_key_to_use = conf.api_key if conf else ""
    else:
        api_key_to_use = encrypt_secret(api_key_to_use) if api_key_to_use else ""

    temp_config = AIConfig(
        provider=data.config.provider, model_name=data.config.model_name,
        api_key=api_key_to_use, base_url=data.config.base_url
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
Guarda en base de datos la clave API v4 de TheTVDB cifrada y el estado de su 
interruptor maestro. Ignora la cadena comodín de asteriscos.
"""
@app.post("/api/ui/system/tvdb")
async def save_tvdb_config(data: TVDBConfigForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if config:
            if data.tvdb_api_key.strip() != "********":
                config.tvdb_api_key = encrypt_secret(data.tvdb_api_key.strip())
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
    
    if clean_key == "********":
        with Session(engine) as session:
            config = session.exec(select(SystemConfig)).first()
            if config and config.tvdb_api_key:
                clean_key = decrypt_secret(config.tvdb_api_key)
            else:
                return {"success": False, "error": "No hay clave guardada para testear."}
    elif not clean_key:
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
# API: SINCRONIZACIÓN CON APLICACIONES ARR
# ==========================================

class ArrSyncForm(BaseModel):
    url: str
    api_key: str
    internal_url: str = ""

"""
Recibe las credenciales de una instancia de Sonarr o Radarr, actualiza 
la configuración del sistema y dispara la sincronización. Soporta cadenas comodín
y sobrescritura de URL interna para redes Docker.
"""
@app.post("/api/ui/system/sync/{app_type}")
async def sync_arr_application(app_type: str, data: ArrSyncForm, request: Request):
    if app_type not in ["sonarr", "radarr"]:
        return {"success": False, "error": "Tipo de aplicación no soportado."}
        
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            return {"success": False, "error": "Configuración del sistema no inicializada."}
            
        config.internal_url = data.internal_url.strip() if data.internal_url else None
            
        if data.api_key != "********":
            real_app_key = data.api_key
            if app_type == "sonarr":
                config.sonarr_url = data.url
                config.sonarr_key = encrypt_secret(real_app_key)
            else:
                config.radarr_url = data.url
                config.radarr_key = encrypt_secret(real_app_key)
        else:
            if app_type == "sonarr":
                config.sonarr_url = data.url
                real_app_key = decrypt_secret(config.sonarr_key) if config.sonarr_key else ""
            else:
                config.radarr_url = data.url
                real_app_key = decrypt_secret(config.radarr_key) if config.radarr_key else ""
            
        session.add(config)
        session.commit()
        
        if config.internal_url:
            kitsunarr_url = config.internal_url.rstrip("/")
            logger.info(f"🌐 Usando URL Interna personalizada para auto-sincronización: {kitsunarr_url}")
        else:
            kitsunarr_url = str(request.base_url).rstrip("/")
            
        kitsunarr_api_key = config.api_key
        
    try:
        from services.arr_manager import sync_indexer_to_arr
        result = await sync_indexer_to_arr(app_type, data.url, real_app_key, kitsunarr_url, kitsunarr_api_key)
        return {"success": result["success"], "error": result.get("error", "")}
    except ImportError:
        return {"success": False, "error": "El servicio de sincronización aún no está implementado."}
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

"""
Recibe y procesa el formulario de configuración de un indexador desde la interfaz web.
Si el usuario elige el método 'Auto-Login', intercepta la petición para robar la cookie 
maestra en tiempo real antes de guardar las credenciales cifradas en la base de datos local.
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
                indexer.cookie_string = encrypt_secret(full_cookie_string)
                indexer.username = data.username
                indexer.password = encrypt_secret(data.password)
            else:
                return {"success": False, "error": "Credenciales incorrectas o el tracker bloqueó la conexión."}
        else:
            indexer.auth_type = "cookie"
            indexer.cookie_string = encrypt_secret(data.cookie_string)
            indexer.username = None
            indexer.password = None

        test_cookie = decrypt_secret(indexer.cookie_string) if indexer.cookie_string else ""
        is_ok = await test_unionfansub_connection(test_cookie)
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
completas descifradas y devolviendo el resultado de red a la UI.
"""
@app.post("/api/ui/indexer/test/{identifier}")
async def test_existing_indexer(identifier: str):
    logger.info(f"🧪 Iniciando test manual de conexión para el indexador: '{identifier}'...")
    
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if not indexer or not indexer.cookie_string:
            logger.error(f"❌ Test fallido: El indexador '{identifier}' no está configurado o falta la cookie.")
            return {"success": False, "status": "error"}
        
        decrypted_cookie = decrypt_secret(indexer.cookie_string)
        is_ok = await test_unionfansub_connection(decrypted_cookie)
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
            cookie = decrypt_secret(indexer.cookie_string) if indexer and indexer.status == "ok" and indexer.cookie_string else ""
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
como clave maestra de Torznab. Además, si Sonarr o Radarr están configurados,
sincroniza automáticamente la nueva clave con ellos para no perder la conexión.
"""
@app.post("/api/ui/system/apikey/regenerate")
async def regenerate_apikey(request: Request):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        new_key = secrets.token_hex(16)
        config.api_key = new_key
        session.commit()
        
        kitsunarr_url = str(request.base_url).rstrip("/")
        sonarr_synced = False
        radarr_synced = False
        
        try:
            from services.arr_manager import sync_indexer_to_arr
            from services.encrypt import decrypt_secret
            
            if config.sonarr_url and config.sonarr_key:
                logger.info("🔄 Regeneración de clave: Auto-sincronizando Sonarr...")
                decrypted_sonarr_key = decrypt_secret(config.sonarr_key)
                res_sonarr = await sync_indexer_to_arr("sonarr", config.sonarr_url, decrypted_sonarr_key, kitsunarr_url, new_key)
                sonarr_synced = res_sonarr.get("success", False)
                
            if config.radarr_url and config.radarr_key:
                logger.info("🔄 Regeneración de clave: Auto-sincronizando Radarr...")
                decrypted_radarr_key = decrypt_secret(config.radarr_key)
                res_radarr = await sync_indexer_to_arr("radarr", config.radarr_url, decrypted_radarr_key, kitsunarr_url, new_key)
                radarr_synced = res_radarr.get("success", False)
                
        except Exception as e:
            logger.error(f"❌ Error en auto-sincronización tras regenerar clave: {e}")

        return {
            "success": True, 
            "new_key": new_key,
            "sonarr_synced": sonarr_synced,
            "radarr_synced": radarr_synced
        }

"""
Función de detención en crudo. Duerme un segundo y medio para 
dar tiempo a devolver la respuesta HTTP antes de forzar la salida.
"""
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