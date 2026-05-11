import os
import asyncio
from contextlib import asynccontextmanager
import jwt
from fastapi.responses import RedirectResponse

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from dotenv import load_dotenv
from sqlmodel import Session, select

from core.database.engine import engine, create_db_and_tables
from core.app.logger import logger
from core.database.models import SystemConfig
from core.app.encrypt import SECRETS_FILE, MASTER_KEY, decrypt_secret
from core.app.background import worker_loop

from api.torznab.torznab import router as torznab_router
from api.ui.router import router as ui_router

load_dotenv()

PORT = int(os.getenv("KITSUNARR_PORT", 4080))
HOST = os.getenv("KITSUNARR_HOST", "0.0.0.0")

# ------------------------------------------------------------
# Arranca Kitsunarr preparando la base de datos, creando la
# configuracion inicial cuando falta y activando los workers de fondo.
# ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🗄️ Inicializando Base de Datos SQLite...")
    create_db_and_tables()
    
    if not os.path.exists(SECRETS_FILE):
        logger.info("🔑 Generando nueva llave maestra en 'secrets.xml'...")
    else:
        logger.info("🔑 Clave de cifrado maestra detectada y cargada exitosamente.")
    
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            logger.info("⚙️ Generando configuración inicial del sistema...")
            import secrets
            session.add(SystemConfig(api_key=secrets.token_hex(16)))
            session.commit()
            
        if not config or not config.admin_password_hash:
            logger.warning("⚠️ No hay usuario administrador. Entrando en modo 'Setup'.")
        else:
            logger.info(f"🛡️ Sistema securizado: Administrador '{config.admin_user}' verificado.")
            
    logger.info("🚀 Arrancando trabajadores de fondo (Workers)...")
    asyncio.create_task(worker_loop())
    logger.info("✅ Kitsunarr Core iniciado correctamente. Listo para recibir peticiones.")
    yield

app = FastAPI(title="Kitsunarr Multi-Indexer", lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ------------------------------------------------------------
# Protege la interfaz, Torznab y las descargas de Kitsunarr validando
# sesion web o API key antes de dejar pasar cada peticion.
# ------------------------------------------------------------
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path

    # ------------------------------------------------------------
    # Comprueba una API key recibida contra la clave guardada por
    # Kitsunarr, tanto si esta cifrada como si procede de versiones
    # antiguas en texto plano.
    # ------------------------------------------------------------
    def _api_key_matches(stored_key: str, provided_key: str) -> bool:
        if not stored_key or not provided_key:
            return False
        return provided_key == stored_key or provided_key == decrypt_secret(stored_key)
    
    public_paths = ["/login", "/setup", "/api/ui/auth/login", "/api/ui/auth/setup", "/api/ui/system/restart"]
    if path.startswith("/static") or path in public_paths:
        return await call_next(request)
        
    if path == "/api":
        provided_apikey = request.query_params.get("apikey", "")
        with Session(engine) as session:
            config = session.exec(select(SystemConfig)).first()
            if not config or not _api_key_matches(config.api_key, provided_apikey):
                logger.warning(f"❌ Acceso denegado a {path} (API Key inválida)")
                return Response(content="<?xml version='1.0' encoding='UTF-8'?><error code='100' description='Invalid API Key'/>", media_type="application/xml", status_code=401)
        return await call_next(request)

    if path.startswith("/api/download"):
        provided_apikey = request.query_params.get("apikey", "")
        token = request.cookies.get("kitsunarr_session")

        has_valid_session = False
        if token:
            try:
                jwt.decode(token, MASTER_KEY, algorithms=["HS256"])
                has_valid_session = True
            except Exception:
                has_valid_session = False

        has_valid_api_key = False
        with Session(engine) as session:
            config = session.exec(select(SystemConfig)).first()
            if config and _api_key_matches(config.api_key, provided_apikey):
                has_valid_api_key = True

        if not has_valid_session and not has_valid_api_key:
            logger.warning(f"❌ Acceso denegado a {path} (sin sesión válida ni API Key válida)")
            return JSONResponse(status_code=401, content={"success": False, "detail": "Acceso denegado"})

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

# ------------------------------------------------------------
# Captura errores no controlados de Kitsunarr y devuelve una respuesta
# JSON uniforme para que la UI pueda mostrar el fallo correctamente.
# ------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"❌ Error crítico en el servidor: {exc}")
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": f"Error interno: {str(exc)}"}
    )

app.include_router(torznab_router)

app.include_router(ui_router)

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host=HOST, 
        port=PORT, 
        reload=True,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
