from fastapi import APIRouter

from api.ui.views import router as views_router
from api.ui.auth import router as auth_router
from api.ui.system import router as system_router
from api.ui.indexers import router as indexers_router
from api.ui.torrents import router as torrents_router
from api.ui.tvdb import router as tvdb_router
from api.ui.ai import router as ai_router
from api.ui.logs import router as logs_router
from api.downloads.download import router as download_router
from api.torznab.torznab import router as torznab_router

router = APIRouter()

router.include_router(views_router)
router.include_router(auth_router)
router.include_router(system_router)
router.include_router(indexers_router)
router.include_router(torrents_router)
router.include_router(tvdb_router)
router.include_router(ai_router)
router.include_router(logs_router)
router.include_router(download_router)
router.include_router(torznab_router)
