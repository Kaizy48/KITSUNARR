import os
from fastapi import APIRouter
from core.app.logger import LOG_FILE

router = APIRouter(prefix="/api/ui/logs", tags=["UI Logs"])

# ------------------------------------------------------------
# Entrega a la consola de eventos de Kitsunarr las últimas líneas
# del log principal para revisar actividad reciente desde la UI.
# ------------------------------------------------------------
@router.get("")
async def get_logs():
    if not os.path.exists(LOG_FILE):
        return {"logs": "Aún no hay eventos registrados."}
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-150:] 
    return {"logs": "".join(lines)}

# ------------------------------------------------------------
# Vacía el log principal cuando el usuario limpia la consola de
# eventos desde el panel de Kitsunarr.
# ------------------------------------------------------------
@router.delete("")
async def clear_logs():
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.truncate(0)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
