from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import IndexerConfig
from core.app.logger import logger
from core.app.encrypt import encrypt_secret, decrypt_secret
from core.app.indexers.manager import indexer_manager

router = APIRouter(prefix="/api/ui/indexer", tags=["UI Indexers"])

# ------------------------------------------------------------
# Datos de configuración de un indexador que el usuario guarda
# desde el panel de Kitsunarr.
# ------------------------------------------------------------
class IndexerForm(BaseModel):
    identifier: str
    name: str
    auth_type: str
    cookie_string: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    api_key: Optional[str] = None

# ------------------------------------------------------------
# Guarda o actualiza un indexador, valida sus credenciales contra
# el tracker y deja la conexión lista para búsquedas de Kitsunarr.
# ------------------------------------------------------------
@router.post("")
async def save_indexer(data: IndexerForm):
    with Session(engine) as session:
        indexer_conf = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == data.identifier)).first()
        is_new = False
        
        if not indexer_conf:
            indexer_conf = IndexerConfig(name=data.name, identifier=data.identifier, auth_type=data.auth_type)
            session.add(indexer_conf)
            is_new = True
            
        indexer_instance = indexer_manager.get_indexer(data.identifier)
        if not indexer_instance:
            return {"success": False, "error": f"Indexador '{data.identifier}' no implementado."}
            
        if data.auth_type == "login":
            if not data.username or not data.password:
                return {"success": False, "error": "Faltan credenciales."}

            full_cookie = await indexer_instance.login(data.username, data.password)
            if full_cookie:
                indexer_conf.auth_type = "login"
                indexer_conf.cookie_string = encrypt_secret(full_cookie)
                indexer_conf.username = data.username
                indexer_conf.password = encrypt_secret(data.password)
            else:
                return {"success": False, "error": "Credenciales incorrectas o bloqueadas."}
        else:
            indexer_conf.auth_type = "cookie"
            if data.cookie_string:
                indexer_conf.cookie_string = encrypt_secret(data.cookie_string)

        test_cookie = decrypt_secret(indexer_conf.cookie_string) if indexer_conf.cookie_string else ""
        is_ok = await indexer_instance.test_connection(test_cookie)
        indexer_conf.status = "ok" if is_ok else "error"
        session.commit()
        
        if not is_ok:
            return {"success": False, "error": "La cookie es inválida."}
        
        action_str = "añadido" if is_new else "actualizado"
        logger.info(f"💾 Indexador '{data.name}' {action_str} y configurado exitosamente.")
        
        return {"success": True, "status": indexer_conf.status}

# ------------------------------------------------------------
# Elimina de Kitsunarr la configuración de un indexador registrado
# por el usuario.
# ------------------------------------------------------------
@router.delete("/{identifier}")
async def delete_indexer(identifier: str):
    with Session(engine) as session:
        idx = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if idx:
            name = idx.name
            session.delete(idx)
            session.commit()
            
            logger.info(f"🗑️ Indexador '{name}' eliminado del sistema.")
            
            return {"success": True}
        return {"success": False}

# ------------------------------------------------------------
# Activa o desactiva un indexador desde la UI y comprueba su estado
# cuando vuelve a quedar habilitado.
# ------------------------------------------------------------
@router.patch("/{identifier}/toggle")
async def toggle_indexer(identifier: str):
    with Session(engine) as session:
        idx = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if not idx: 
            return {"success": False}
        
        idx.is_enabled = not idx.is_enabled
        
        if idx.is_enabled:
            logger.info(f"▶️ Indexador '{idx.name}' HABILITADO por el usuario.")
            
            instance = indexer_manager.get_indexer(identifier)
            if instance:
                cookie = decrypt_secret(idx.cookie_string) if idx.cookie_string else ""
                is_ok = await instance.test_connection(cookie)
                idx.status = "ok" if is_ok else "error"
                if is_ok:
                    logger.info(f"✅ Auto-test exitoso al activar '{idx.name}'.")
                else:
                    logger.error(f"❌ Auto-test fallido al activar '{idx.name}'.")
        else:
            idx.status = "error"
            logger.info(f"⏸️ Indexador '{idx.name}' DESHABILITADO por el usuario.")

        session.add(idx)
        session.commit()
        session.refresh(idx)
        
        return {
            "success": True, 
            "is_enabled": idx.is_enabled, 
            "status": idx.status
        }

# ------------------------------------------------------------
# Ejecuta una prueba manual de conexión con el tracker configurado
# y renueva la cookie cuando el indexador permite login automático.
# ------------------------------------------------------------
@router.post("/test/{identifier}")
async def test_indexer(identifier: str):
    with Session(engine) as session:
        idx = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == identifier)).first()
        if not idx: 
            logger.error(f"❌ Test fallido: El indexador '{identifier}' no existe en la base de datos.")
            return {"success": False}
        
        logger.info(f"🧪 Iniciando test manual de conexión para el indexador: '{idx.name}'...")
            
        instance = indexer_manager.get_indexer(identifier)
        if not instance: 
            logger.error(f"❌ Test fallido: No hay un scraper programado para '{idx.name}'.")
            return {"success": False}

        cookie = decrypt_secret(idx.cookie_string) if idx.cookie_string else ""
        is_ok = await instance.test_connection(cookie)

        if not is_ok and idx.auth_type == "login" and idx.username and idx.password:
            logger.warning(f"🔄 La cookie actual falló. Intentando Auto-Login para '{idx.name}'...")
            new_cookie = await instance.login(idx.username, decrypt_secret(idx.password))
            if new_cookie:
                idx.cookie_string = encrypt_secret(new_cookie)
                is_ok = await instance.test_connection(new_cookie)

        idx.status = "ok" if is_ok else "error"
        session.commit()
        
        if is_ok:
            logger.info(f"✅ Test exitoso: Conexión establecida y cookie válida para '{idx.name}'.")
        else:
            logger.error(f"❌ Test fallido: La cookie ha expirado o el tracker bloquea la conexión para '{idx.name}'.")
            
        return {"success": is_ok, "status": idx.status}
