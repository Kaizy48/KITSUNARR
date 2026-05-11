import os
import time
import asyncio
import tvdb_v4_official
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import Optional

from core.database.engine import engine
from core.database.models import SystemConfig, AIConfig
from core.app.encrypt import encrypt_secret, decrypt_secret
from core.app.logger import logger
from core.app.background import wake_worker
from services.arr.manager import sync_indexer_to_arr

router = APIRouter(prefix="/api/ui/system", tags=["UI System"])

# ------------------------------------------------------------
# Datos generales del sistema que se guardan desde Configuración:
# URLs internas, claves Arr, TheTVDB y credenciales qBittorrent.
# ------------------------------------------------------------
class SystemConfigForm(BaseModel):
    internal_url: Optional[str] = None
    tvdb_api_key: Optional[str] = None
    tvdb_is_enabled: bool = False
    sonarr_url: Optional[str] = None
    sonarr_key: Optional[str] = None
    radarr_url: Optional[str] = None
    radarr_key: Optional[str] = None
    qbittorrent_url: Optional[str] = None
    qbittorrent_user: Optional[str] = None
    qbittorrent_password: Optional[str] = None

# ------------------------------------------------------------
# Guarda la configuración general de Kitsunarr y cifra las claves
# sensibles antes de persistirlas.
# ------------------------------------------------------------
@router.post("/config")
async def save_system_config(data: SystemConfigForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            return {"success": False, "error": "Configuración maestra no encontrada."}

        config.internal_url = data.internal_url
        if data.tvdb_api_key and data.tvdb_api_key.strip() != "********":
            config.tvdb_api_key = encrypt_secret(data.tvdb_api_key.strip())
        config.tvdb_is_enabled = data.tvdb_is_enabled

        config.sonarr_url = data.sonarr_url
        if data.sonarr_key and data.sonarr_key.strip() != "********":
            config.sonarr_key = encrypt_secret(data.sonarr_key.strip())

        config.radarr_url = data.radarr_url
        if data.radarr_key and data.radarr_key.strip() != "********":
            config.radarr_key = encrypt_secret(data.radarr_key.strip())

        config.qbittorrent_url = data.qbittorrent_url
        config.qbittorrent_user = data.qbittorrent_user
        if data.qbittorrent_password and data.qbittorrent_password.strip() != "":
            config.qbittorrent_password = encrypt_secret(data.qbittorrent_password.strip())

        session.add(config)
        session.commit()
        return {"success": True}

# ------------------------------------------------------------
# Regenera la API key pública de Kitsunarr y vuelve a sincronizar
# el indexador en Sonarr/Radarr cuando están configurados.
# ------------------------------------------------------------
@router.post("/apikey/regenerate")
async def regenerate_api_key():
    import secrets
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            return {"success": False}

        new_key = secrets.token_hex(16)
        config.api_key = encrypt_secret(new_key)
        session.add(config)
        session.commit()

        s_synced, r_synced = False, False
        if config.sonarr_url and config.sonarr_key:
            res = await sync_indexer_to_arr("sonarr", config.sonarr_url, decrypt_secret(config.sonarr_key), config.internal_url or "http://localhost:4080", new_key)
            s_synced = res.get("success", False)
        if config.radarr_url and config.radarr_key:
            res = await sync_indexer_to_arr("radarr", config.radarr_url, decrypt_secret(config.radarr_key), config.internal_url or "http://localhost:4080", new_key)
            r_synced = res.get("success", False)

        return {"success": True, "api_key": new_key, "sonarr_synced": s_synced, "radarr_synced": r_synced}

# ------------------------------------------------------------
# Detiene el proceso de Kitsunarr después de responder a la UI para
# permitir un reinicio externo del servicio o contenedor.
# ------------------------------------------------------------
def force_restart():
    time.sleep(1.5)
    os._exit(0)

# ------------------------------------------------------------
# Solicita un reinicio controlado desde la interfaz de Kitsunarr.
# ------------------------------------------------------------
@router.post("/restart")
async def restart_system(background_tasks: BackgroundTasks):
    logger.warning("🔄 Solicitud de reinicio recibida. Deteniendo sistema...")
    background_tasks.add_task(force_restart)
    return {"success": True}

# ------------------------------------------------------------
# Devuelve el estado básico del sistema que la UI necesita para
# reflejar interruptores y servicios activos.
# ------------------------------------------------------------
@router.get("/status")
async def get_system_status():
    with Session(engine) as session:
        sys = session.exec(select(SystemConfig)).first()
        return {"tvdb_is_enabled": sys.tvdb_is_enabled if sys else False}

# ------------------------------------------------------------
# Estado de los interruptores principales de IA que controlan si
# Kitsunarr permite uso manual y procesamiento automático.
# ------------------------------------------------------------
class AdvancedSettingsForm(BaseModel):
    is_enabled: bool
    is_automated: bool

# ------------------------------------------------------------
# Guarda el estado de la IA y despierta el worker cuando el usuario
# activa el procesado automático.
# ------------------------------------------------------------
@router.post("/advanced")
async def save_advanced_settings(data: AdvancedSettingsForm):
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
        if not ai_config:
            ai_config = AIConfig()
        ai_config.is_enabled = data.is_enabled
        ai_config.is_automated = data.is_automated
        session.add(ai_config)
        session.commit()

        estado = "Activada" if data.is_enabled else "Desactivada"
        logger.info(f"⚙️ IA {estado}. Modo Automático: {'ON' if data.is_automated else 'OFF'}")
        if data.is_enabled and data.is_automated:
            wake_worker("activación de worker IA en Configuración")
        return {"success": True}

# ------------------------------------------------------------
# Datos para crear o actualizar el indexador Torznab de Kitsunarr
# dentro de una aplicación Arr.
# ------------------------------------------------------------
class SyncArrForm(BaseModel):
    url: str
    api_key: str
    internal_url: Optional[str] = None

# ------------------------------------------------------------
# Sincroniza Kitsunarr como indexador Torznab en Sonarr o Radarr
# usando la URL interna y la API key configuradas por el usuario.
# ------------------------------------------------------------
@router.post("/sync/{app_type}")
async def sync_arr_app(app_type: str, data: SyncArrForm):
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        if not sys_config:
            return {"success": False, "error": "Sistema no configurado."}

        if app_type == "sonarr":
            sys_config.sonarr_url = data.url
        else:
            sys_config.radarr_url = data.url

        arr_key_plain = data.api_key
        if data.api_key != "********":
            encrypted_key = encrypt_secret(data.api_key)
            if app_type == "sonarr":
                sys_config.sonarr_key = encrypted_key
            else:
                sys_config.radarr_key = encrypted_key
        else:
            stored_key = sys_config.sonarr_key if app_type == "sonarr" else sys_config.radarr_key
            arr_key_plain = decrypt_secret(stored_key) if stored_key else ""

        sys_config.internal_url = data.internal_url
        session.commit()

        kitsunarr_key_plain = decrypt_secret(sys_config.api_key)
        kitsunarr_url = data.internal_url if data.internal_url else "http://localhost:4080"

        res = await sync_indexer_to_arr(app_type, data.url, arr_key_plain, kitsunarr_url, kitsunarr_key_plain)
        if res.get("success"):
            return {"success": True, "warning": res.get("warning")}
        return {"success": False, "error": res.get("error", "Error desconocido.")}

# ------------------------------------------------------------
# Datos temporales para probar la conexión de Kitsunarr con
# qBittorrent antes de guardar la configuración.
# ------------------------------------------------------------
class QBTestForm(BaseModel):
    qbittorrent_url: str
    qbittorrent_user: str
    qbittorrent_password: str

# ------------------------------------------------------------
# Comprueba que Kitsunarr puede iniciar sesión en qBittorrent con
# las credenciales indicadas o ya guardadas.
# ------------------------------------------------------------
@router.post("/qbittorrent/test")
async def test_qbittorrent(data: QBTestForm):
    from services.torrent.qbittorrent import qbittorrent_login
    pw = data.qbittorrent_password
    if pw == "********":
        with Session(engine) as session:
            sys_conf = session.exec(select(SystemConfig)).first()
            if sys_conf and sys_conf.qbittorrent_password:
                pw = decrypt_secret(sys_conf.qbittorrent_password)

    success = await qbittorrent_login(data.qbittorrent_url, data.qbittorrent_user, pw)
    if success:
        return {"success": True}
    return {"success": False, "error": "Credenciales inválidas o qBittorrent inaccesible."}

# ------------------------------------------------------------
# Datos persistentes de qBittorrent usados para consultar el estado
# de descargas asociadas a fichas de Kitsunarr.
# ------------------------------------------------------------
class QBConfigForm(BaseModel):
    qbittorrent_url: str
    qbittorrent_user: str
    qbittorrent_password: str

# ------------------------------------------------------------
# Guarda la conexión de qBittorrent para mostrar progreso, subida,
# ratio y estado de torrents vinculados.
# ------------------------------------------------------------
@router.post("/qbittorrent")
async def save_qbittorrent(data: QBConfigForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            return {"success": False, "error": "Sistema no configurado."}

        config.qbittorrent_url = data.qbittorrent_url
        config.qbittorrent_user = data.qbittorrent_user
        if data.qbittorrent_password != "********":
            config.qbittorrent_password = encrypt_secret(data.qbittorrent_password)

        session.commit()
        logger.info("💾 Configuración de qBittorrent actualizada exitosamente.")
        return {"success": True}

# ------------------------------------------------------------
# Datos de TheTVDB que permiten activar o desactivar el enriquecido
# de metadatos en Kitsunarr.
# ------------------------------------------------------------
class TVDBConfigForm(BaseModel):
    tvdb_api_key: str
    tvdb_is_enabled: bool = False

# ------------------------------------------------------------
# Guarda la API key y el estado de TheTVDB para que Kitsunarr pueda
# identificar series y descargar metadatos maestros.
# ------------------------------------------------------------
@router.post("/tvdb")
async def save_tvdb_config(data: TVDBConfigForm):
    with Session(engine) as session:
        config = session.exec(select(SystemConfig)).first()
        if not config:
            return {"success": False, "error": "Configuración del sistema no inicializada."}

        if data.tvdb_api_key and data.tvdb_api_key.strip() != "********":
            config.tvdb_api_key = encrypt_secret(data.tvdb_api_key.strip())

        config.tvdb_is_enabled = data.tvdb_is_enabled
        session.add(config)
        session.commit()

        estado = "ON" if data.tvdb_is_enabled else "OFF"
        logger.info(f"📡 Configuración de TheTVDB guardada localmente. (Estado: {estado})")
        return {"success": True}


# ------------------------------------------------------------
# Datos temporales para validar la conexión con TheTVDB desde el
# panel antes de activar el servicio.
# ------------------------------------------------------------
class TVDBTestForm(BaseModel):
    tvdb_api_key: str
    tvdb_is_enabled: bool = False

# ------------------------------------------------------------
# Prueba la autenticación de Kitsunarr contra TheTVDB y confirma
# que la API key configurada puede realizar búsquedas.
# ------------------------------------------------------------
@router.post("/tvdb/test")
async def test_tvdb_connection(data: TVDBTestForm):
    api_key_to_use = (data.tvdb_api_key or "").strip()
    if api_key_to_use == "********":
        with Session(engine) as session:
            sys_conf = session.exec(select(SystemConfig)).first()
            if sys_conf and sys_conf.tvdb_api_key:
                api_key_to_use = decrypt_secret(sys_conf.tvdb_api_key)
                api_key_to_use = (api_key_to_use or "").strip()

    if not api_key_to_use or api_key_to_use == "********":
        return {"success": False, "error": "No hay API Key configurada."}

    try:
        tvdb_pin = (os.getenv("KITSUNARR_TVDB_PIN", "") or "").strip()
        tvdb = tvdb_v4_official.TVDB(api_key_to_use, pin=tvdb_pin) if tvdb_pin else tvdb_v4_official.TVDB(api_key_to_use)

        await asyncio.to_thread(tvdb.search, "Naruto", type="series", limit=1)
        return {"success": True}
    except Exception as e:
        logger.error(f"❌ Fallo de login en TheTVDB: {str(e)}")
        return {"success": False, "error": f"Credenciales inválidas o caducadas: {str(e)}"}
