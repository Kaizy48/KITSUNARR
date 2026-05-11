import os
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import SystemConfig, IndexerConfig, AIConfig
from core.app.encrypt import decrypt_secret

router = APIRouter(tags=["UI Views"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# ------------------------------------------------------------
# Renderiza el asistente inicial para crear el administrador de
# Kitsunarr cuando todavía no existe una instalación protegida.
# ------------------------------------------------------------
@router.get("/setup")
async def render_setup(request: Request):
    return templates.TemplateResponse(request=request, name="views/setup.html")

# ------------------------------------------------------------
# Renderiza la pantalla de acceso al panel de Kitsunarr.
# ------------------------------------------------------------
@router.get("/login")
async def render_login(request: Request):
    return templates.TemplateResponse(request=request, name="views/login.html")

# ------------------------------------------------------------
# Renderiza la vista principal de indexadores con las conexiones
# configuradas por el usuario.
# ------------------------------------------------------------
@router.get("/")
@router.get("/indexers")
async def render_indexers(request: Request):
    with Session(engine) as session:
        indexers = session.exec(select(IndexerConfig)).all()
    return templates.TemplateResponse(
        request=request,
        name="views/indexers.html",
        context={"indexers": indexers}
    )

# ------------------------------------------------------------
# Renderiza la caché local de torrents que Kitsunarr ha descubierto
# desde los trackers.
# ------------------------------------------------------------
@router.get("/cache")
async def render_cache(request: Request):
    return templates.TemplateResponse(request=request, name="views/cache.html")

# ------------------------------------------------------------
# Renderiza la consola de eventos para revisar la actividad reciente
# del sistema.
# ------------------------------------------------------------
@router.get("/events")
async def render_events(request: Request):
    return templates.TemplateResponse(request=request, name="views/events.html")

# ------------------------------------------------------------
# Renderiza la ficha técnica detallada de un torrent de la caché.
# ------------------------------------------------------------
@router.get("/cache/torrent/{guid}")
async def render_torrent_detail(request: Request, guid: str):
    return templates.TemplateResponse(
        request=request,
        name="views/tracker_torrent.html",
        context={"guid": guid}
    )

# ------------------------------------------------------------
# Renderiza el editor manual de una ficha de torrent cacheada.
# ------------------------------------------------------------
@router.get("/cache/edit/{guid}")
async def render_torrent_edit(request: Request, guid: str):
    return templates.TemplateResponse(
        request=request,
        name="views/edit_torrent.html",
        context={"guid": guid}
    )

# ------------------------------------------------------------
# Renderiza la estantería de una serie TVDB vinculada a torrents
# locales.
# ------------------------------------------------------------
@router.get("/cache/series/{tvdb_id}")
async def render_series_shelf(request: Request, tvdb_id: str):
    return templates.TemplateResponse(
        request=request,
        name="views/series.html",
        context={"tvdb_id": tvdb_id}
    )

# ------------------------------------------------------------
# Renderiza la búsqueda interactiva contra los trackers configurados.
# ------------------------------------------------------------
@router.get("/search")
async def render_search(request: Request):
    return templates.TemplateResponse(request=request, name="views/search.html")

# ------------------------------------------------------------
# Renderiza la biblioteca local de series descargadas desde TheTVDB.
# ------------------------------------------------------------
@router.get("/tvdb_cache")
async def render_tvdb_cache(request: Request):
    return templates.TemplateResponse(request=request, name="views/tvdb_cache.html")

# ------------------------------------------------------------
# Renderiza la búsqueda manual de series en TheTVDB.
# ------------------------------------------------------------
@router.get("/tvdb_search")
async def render_tvdb_search(request: Request):
    return templates.TemplateResponse(request=request, name="views/tvdb_search.html")

# ------------------------------------------------------------
# Renderiza el laboratorio de torrents para pruebas y vinculación
# manual de fichas.
# ------------------------------------------------------------
@router.get("/torrent_lab")
async def render_torrent_lab(request: Request):
    return templates.TemplateResponse(request=request, name="views/torrent_lab.html")

# ------------------------------------------------------------
# Renderiza el laboratorio de IA con la configuración actual del
# proveedor ocultando credenciales sensibles.
# ------------------------------------------------------------
@router.get("/ai")
async def render_ai_lab(request: Request):
    with Session(engine) as session:
        ai_config = session.exec(select(AIConfig)).first()
    if ai_config and ai_config.api_key:
        ai_config.api_key = "********"
    return templates.TemplateResponse(
        request=request,
        name="views/ai_lab.html",
        context={"ai_config": ai_config}
    )

# ------------------------------------------------------------
# Renderiza la configuración general de Kitsunarr con claves
# sensibles enmascaradas y la API key propia disponible para copiar.
# ------------------------------------------------------------
@router.get("/config")
async def render_config(request: Request):
    with Session(engine) as session:
        sys_config = session.exec(select(SystemConfig)).first()
        ai_config = session.exec(select(AIConfig)).first()

    api_key_plain = ""
    if sys_config and sys_config.api_key:
        api_key_plain = decrypt_secret(sys_config.api_key)

    if sys_config:
        if sys_config.tvdb_api_key: sys_config.tvdb_api_key = "********"
        if sys_config.sonarr_key: sys_config.sonarr_key = "********"
        if sys_config.radarr_key: sys_config.radarr_key = "********"

    if ai_config and ai_config.api_key:
        ai_config.api_key = "********"

    return templates.TemplateResponse(
        request=request,
        name="views/config.html",
        context={
            "sys_config": sys_config,
            "ai_config": ai_config,
            "api_key": api_key_plain,
        }
    )
