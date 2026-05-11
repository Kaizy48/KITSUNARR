from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import AIConfig, AIModel
from core.app.encrypt import encrypt_secret, decrypt_secret
from services.ai.engine import test_ai_connection, test_single_torrent_ai, process_pending_torrents

router = APIRouter(prefix="/api/ui/ai", tags=["UI AI"])

# ------------------------------------------------------------
# Datos de configuración del proveedor de IA que se guardan desde
# el panel de Kitsunarr.
# ------------------------------------------------------------
class AIConfigForm(BaseModel):
    provider: str
    model_name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    rpm_limit: int = 15
    tpm_limit: int = 250000
    rpd_limit: int = 200

# ------------------------------------------------------------
# Lote de fichas seleccionadas por el usuario para forzar su
# procesamiento con IA desde la interfaz.
# ------------------------------------------------------------
class ForceBatchForm(BaseModel):
    guids: List[str]


# ------------------------------------------------------------
# Comprueba si el motor de IA está disponible para acciones
# manuales del laboratorio y de la caché local.
# ------------------------------------------------------------
def _assert_ai_enabled_for_manual_usage(session: Session):
    ai_db = session.exec(select(AIConfig)).first()
    if not ai_db or not ai_db.is_enabled:
        return False
    return True

# ------------------------------------------------------------
# Devuelve la configuración actual de IA al panel, ocultando la
# clave privada para que no se muestre completa en el navegador.
# ------------------------------------------------------------
@router.get("/config")
async def get_ai_config():
    with Session(engine) as session:
        config = session.exec(select(AIConfig)).first()
        if not config:
            config = AIConfig()
            session.add(config)
            session.commit()
            
        data = config.dict()
        data["api_key"] = "********" if config.api_key else ""
        return {"success": True, "config": data}

# ------------------------------------------------------------
# Guarda la configuración del proveedor de IA y sus límites de uso
# para que Kitsunarr normalice fichas con el modelo seleccionado.
# ------------------------------------------------------------
@router.post("/config")
async def save_ai_config(data: AIConfigForm):
    with Session(engine) as session:
        config = session.exec(select(AIConfig)).first()
        if not config: config = AIConfig()
            
        config.provider = data.provider
        config.model_name = data.model_name
        config.base_url = data.base_url
        config.rpm_limit = data.rpm_limit
        config.tpm_limit = data.tpm_limit
        config.rpd_limit = data.rpd_limit
        
        if data.api_key and data.api_key.strip() != "********":
            config.api_key = encrypt_secret(data.api_key.strip())
            
        session.add(config)
        session.commit()
        model_key = f"{(data.provider or '').strip().lower()}:{(data.model_name or '').strip().lower()}"
        model = session.exec(select(AIModel).where(AIModel.model_key == model_key)).first()
        if not model:
            model = AIModel(
                model_key=model_key,
                provider=data.provider,
                model_name=data.model_name,
                rpm_limit=int(data.rpm_limit or 4),
                tpm_limit=int(data.tpm_limit or 250000),
                rpd_limit=int(data.rpd_limit or 20),
            )
            session.add(model)
        else:
            model.provider = data.provider
            model.model_name = data.model_name
            model.rpm_limit = int(data.rpm_limit or model.rpm_limit)
            model.tpm_limit = int(data.tpm_limit or model.tpm_limit)
            model.rpd_limit = int(data.rpd_limit or model.rpd_limit)
            session.add(model)
        session.commit()
        return {"success": True}

# ------------------------------------------------------------
# Petición para reiniciar cuotas del proveedor de IA globalmente
# o solo para un modelo concreto.
# ------------------------------------------------------------
class ResetForm(BaseModel):
    provider: Optional[str] = None
    model_name: Optional[str] = None

# ------------------------------------------------------------
# Configuración temporal enviada por el laboratorio para probar la
# conexión de IA sin depender solo de lo persistido en la base.
# ------------------------------------------------------------
class AIPingForm(BaseModel):
    config: dict


# ------------------------------------------------------------
# Prepara una configuración de IA en memoria a partir del formulario
# del panel para ejecutar pings o pruebas de normalización.
# ------------------------------------------------------------
def _runtime_ai_config_from_payload(conf: dict, db_config: AIConfig = None) -> AIConfig:
    rpm_limit = conf.get("rpm_limit")
    tpm_limit = conf.get("tpm_limit")
    rpd_limit = conf.get("rpd_limit")

    if rpm_limit is None and db_config:
        rpm_limit = db_config.rpm_limit
    if tpm_limit is None and db_config:
        tpm_limit = db_config.tpm_limit
    if rpd_limit is None and db_config:
        rpd_limit = db_config.rpd_limit

    return AIConfig(
        provider=conf.get("provider", "ollama"),
        model_name=conf.get("model_name", "llama3"),
        api_key=conf.get("api_key") or None,
        base_url=conf.get("base_url") or None,
        custom_prompt=conf.get("custom_prompt"),
        rpm_limit=int(rpm_limit) if rpm_limit is not None else 5,
        tpm_limit=int(tpm_limit) if tpm_limit is not None else 250000,
        rpd_limit=int(rpd_limit) if rpd_limit is not None else 20,
    )

# ------------------------------------------------------------
# Prueba la conexión con el proveedor de IA configurado y devuelve
# al panel si Kitsunarr puede usar ese modelo.
# ------------------------------------------------------------
@router.post("/ping")
async def test_ai_provider(data: AIPingForm):
    conf = data.config
    ai_db = None
    with Session(engine) as session:
        ai_db = session.exec(select(AIConfig)).first()
        if not _assert_ai_enabled_for_manual_usage(session):
            return {"success": False, "error": "El motor de IA está desactivado. Actívalo en Configuración para usar Ping."}

    if conf.get("api_key") == "********":
        conf["api_key"] = decrypt_secret(ai_db.api_key) if ai_db and ai_db.api_key else ""

    runtime_conf = _runtime_ai_config_from_payload(conf, db_config=ai_db)
    test_result = await test_ai_connection(runtime_conf)
    if test_result.get("success"):
        provider = runtime_conf.provider.upper() if runtime_conf.provider else "IA"
        return {"success": True, "result": f"Ping correcto con {provider}."}
    return {"success": False, "error": test_result.get("error", "Error desconocido en test de conexión.")}

# ------------------------------------------------------------
# Datos de una ficha concreta para ejecutar una prueba real de
# normalización con IA desde el laboratorio de Kitsunarr.
# ------------------------------------------------------------
class AITestForm(BaseModel):
    guid: str
    config: dict

# ------------------------------------------------------------
# Ejecuta una prueba de IA sobre una ficha específica para revisar
# el título normalizado antes de procesarla automáticamente.
# ------------------------------------------------------------
@router.post("/test")
async def run_ai_test(data: AITestForm):
    conf = data.config
    ai_db = None
    with Session(engine) as session:
        ai_db = session.exec(select(AIConfig)).first()
        if not _assert_ai_enabled_for_manual_usage(session):
            return {"success": False, "error": "El motor de IA está desactivado. Actívalo en Configuración para usar el laboratorio."}

    if conf.get("api_key") == "********":
        conf["api_key"] = decrypt_secret(ai_db.api_key) if ai_db and ai_db.api_key else ""

    runtime_conf = _runtime_ai_config_from_payload(conf, db_config=ai_db)
    return await test_single_torrent_ai(data.guid, runtime_conf)

# ------------------------------------------------------------
# Fuerza el procesamiento de IA de fichas seleccionadas por el
# usuario desde la caché local de Kitsunarr.
# ------------------------------------------------------------
@router.post("/force_specific")
async def force_specific_ai(data: ForceBatchForm):
    with Session(engine) as session:
        if not _assert_ai_enabled_for_manual_usage(session):
            return {"success": False, "error": "El motor de IA está desactivado. Actívalo en Configuración para procesar fichas."}
    await process_pending_torrents(specific_guids=data.guids)
    return {"success": True}


# ------------------------------------------------------------
# Devuelve límites, consumo y estado de espera del modelo de IA
# activo para que el panel muestre cuotas y bloqueos temporales.
# ------------------------------------------------------------
@router.get("/model_limits")
async def get_model_limits(provider: str, model_name: str):
    from services.ai.engine import get_ai_model_backoff_state
    key = f"{(provider or '').strip().lower()}:{(model_name or '').strip().lower()}"
    if not provider or not model_name:
        return {"success": False, "error": "Proveedor y modelo son obligatorios."}

    with Session(engine) as session:
        model = session.exec(select(AIModel).where(AIModel.model_key == key)).first()
        ai_cfg = session.exec(select(AIConfig)).first()

        if model:
            return {
                "success": True,
                "limits": {
                    "rpm_limit": int(model.rpm_limit or 4),
                    "tpm_limit": int(model.tpm_limit or 250000),
                    "rpd_limit": int(model.rpd_limit or 20),
                },
                "stats": {
                    "daily_date": model.daily_date,
                    "daily_count": int(model.daily_count or 0),
                    "minute_window_start": model.minute_window_start,
                    "minute_requests": int(model.minute_requests or 0),
                    "minute_tokens": int(model.minute_tokens or 0),
                },
                "backoff": get_ai_model_backoff_state(key)
            }

        return {
            "success": True,
            "limits": {
                "rpm_limit": int((ai_cfg.rpm_limit if ai_cfg else 5) or 5),
                "tpm_limit": int((ai_cfg.tpm_limit if ai_cfg else 250000) or 250000),
                "rpd_limit": int((ai_cfg.rpd_limit if ai_cfg else 20) or 20),
            },
            "stats": None,
            "backoff": get_ai_model_backoff_state(key)
        }

# ------------------------------------------------------------
# Datos para guardar el prompt personalizado que Kitsunarr usará
# como instrucción maestra para normalizar torrents.
# ------------------------------------------------------------
class PromptForm(BaseModel):
    custom_prompt: Optional[str] = None

# ------------------------------------------------------------
# Guarda el prompt personalizado de IA para adaptar la normalización
# de títulos y metadatos a la forma de trabajar del usuario.
# ------------------------------------------------------------
@router.post("/prompt")
async def save_prompt(data: PromptForm):
    with Session(engine) as session:
        config = session.exec(select(AIConfig)).first()
        if not config: config = AIConfig()
        config.custom_prompt = data.custom_prompt
        session.add(config)
        session.commit()
        return {"success": True}

# ------------------------------------------------------------
# Reinicia los contadores de cuota de IA para desbloquear pruebas
# o procesados cuando el usuario lo solicita desde el panel.
# ------------------------------------------------------------
@router.post("/reset_quota")
async def reset_ai_quota(data: ResetForm | None = None):
    from services.ai.engine import reset_daily_quota
    if data and data.provider and data.model_name:
        key = f"{data.provider.strip().lower()}:{data.model_name.strip().lower()}"
        reset_daily_quota(model_key=key)
    else:
        reset_daily_quota()
    return {"success": True}
