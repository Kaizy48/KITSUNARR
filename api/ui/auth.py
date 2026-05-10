from datetime import datetime, timedelta
import jwt
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import SystemConfig
from core.app.encrypt import MASTER_KEY, hash_password, verify_password
from core.app.logger import logger

router = APIRouter(prefix="/api/ui/auth", tags=["UI Auth"])

# ------------------------------------------------------------
# Datos de acceso que envía el formulario de inicio de sesión del
# panel de Kitsunarr.
# ------------------------------------------------------------
class LoginForm(BaseModel):
    username: str
    password: str

# ------------------------------------------------------------
# Datos iniciales para crear el primer administrador durante el
# asistente de configuración de Kitsunarr.
# ------------------------------------------------------------
class SetupForm(BaseModel):
    admin_user: str
    admin_password: str

# ------------------------------------------------------------
# Crea el primer administrador de Kitsunarr y deja la instalación
# protegida para empezar a usar el panel web.
# ------------------------------------------------------------
@router.post("/setup")
async def setup_system(data: SetupForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if config and config.admin_password_hash:
            return {"success": False, "error": "El sistema ya ha sido configurado previamente."}

        if not config:
            import secrets
            config = SystemConfig(api_key=secrets.token_hex(16))
            session.add(config)

        config.admin_user = data.admin_user
        config.admin_password_hash = hash_password(data.admin_password)
        session.commit()
        
        logger.info(f"🛡️ Sistema securizado. Administrador '{data.admin_user}' creado con éxito.")
        return {"success": True, "redirect": "/login"}

# ------------------------------------------------------------
# Valida las credenciales del administrador y abre una sesión web
# para acceder a la interfaz de Kitsunarr.
# ------------------------------------------------------------
@router.post("/login")
async def login(data: LoginForm, response: Response):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        
        if not config or not config.admin_password_hash:
            return {"success": False, "error": "El sistema no está configurado."}
            
        if data.username != config.admin_user or not verify_password(data.password, config.admin_password_hash):
            logger.warning(f"⚠️ Intento de inicio de sesión fallido para el usuario '{data.username}'.")
            return {"success": False, "error": "Credenciales incorrectas"}
            
        expiration = datetime.utcnow() + timedelta(days=7)
        token_data = {"sub": data.username, "exp": expiration}
        token = jwt.encode(token_data, MASTER_KEY, algorithm="HS256")
        
        response = JSONResponse(content={"success": True, "redirect": "/"})
        response.set_cookie(
            key="kitsunarr_session", 
            value=token, 
            httponly=True, 
            max_age=7*24*3600,
            samesite="lax"
        )
        
        logger.info(f"✅ Inicio de sesión exitoso: '{data.username}'.")
        return response

# ------------------------------------------------------------
# Cierra la sesión del administrador y devuelve al usuario a la
# pantalla de login de Kitsunarr.
# ------------------------------------------------------------
@router.post("/logout")
async def logout(response: Response):
    response = JSONResponse(content={"success": True, "redirect": "/login"})
    response.delete_cookie("kitsunarr_session")
    logger.info("👋 Sesión cerrada correctamente.")
    return response
