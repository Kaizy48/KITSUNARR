import asyncio

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlmodel import Session, select, delete

from core.database.engine import engine
from core.database.models import SystemConfig, TorrentCache, TVDBCache, TorrentTVDBCandidates, TVDBEpisodes
from core.app.encrypt import decrypt_secret
from core.app.logger import logger

from services.tvdb.tvdb_api import search_tvdb, fetch_full_tvdb_series

router = APIRouter(prefix="/api/ui", tags=["UI TVDB"])

# ------------------------------------------------------------
# Datos usados para vincular manualmente una ficha torrent con una
# serie concreta de TheTVDB.
# ------------------------------------------------------------
class LinkTvdbForm(BaseModel):
    tvdb_id: str

# ------------------------------------------------------------
# Devuelve la biblioteca local de fichas maestras TheTVDB que
# Kitsunarr ha descargado completas.
# ------------------------------------------------------------
@router.get("/tvdb_cache")
async def get_tvdb_cache_list():
    with Session(engine) as session:
        tvdb_items = session.exec(select(TVDBCache).where(TVDBCache.is_full_record == True).order_by(TVDBCache.series_name_es)).all()
        return {"tvdb_cache": jsonable_encoder(tvdb_items)}

# ------------------------------------------------------------
# Devuelve los episodios locales de una serie TVDB para mostrarlos
# en la ficha de biblioteca.
# ------------------------------------------------------------
@router.get("/tvdb_cache/{tvdb_id}/episodes")
async def get_tvdb_episodes(tvdb_id: str):
    with Session(engine) as session:
        eps = session.exec(select(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == tvdb_id).order_by(TVDBEpisodes.season_number, TVDBEpisodes.episode_number)).all()
        return {"success": True, "episodes": jsonable_encoder(eps)}

# ------------------------------------------------------------
# Elimina una ficha maestra de TheTVDB y, si el usuario lo confirma,
# también borra las fichas torrent vinculadas.
# ------------------------------------------------------------
@router.delete("/tvdb_cache/{tvdb_id}")
async def delete_tvdb_cache_entry(tvdb_id: str, cascade_linked_torrents: bool = Query(default=False)):
    with Session(engine) as session:
        t = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id)).first()
        if not t:
            logger.warning(f"⚠️ [TVDB] Intento de borrar ficha maestra inexistente: TVDB={tvdb_id}")
            return {"success": False, "error": "Ficha maestra no encontrada."}

        linked_torrents = session.exec(select(TorrentCache).where(TorrentCache.tvdb_id == tvdb_id)).all()
        linked_count = len(linked_torrents)

        if linked_count > 0 and not cascade_linked_torrents:
            logger.warning(
                f"⛔ [TVDB] Borrado bloqueado: TVDB={tvdb_id} tiene {linked_count} fichas torrent vinculadas."
            )
            return {
                "success": False,
                "blocked": True,
                "linked_torrents_count": linked_count,
                "error": (
                    f"No se puede borrar solo la ficha maestra TVDB {tvdb_id} porque tiene {linked_count} "
                    f"fichas torrent vinculadas."
                ),
                "hint": "Confirma borrado en cascada para eliminar la ficha maestra y sus fichas torrent vinculadas."
            }

        logger.info(
            f"🗑️ [TVDB] Eliminando ficha maestra: TVDB={tvdb_id} | Título='{(t.series_name_es or t.series_name_original or 'Sin título').strip()}' | Torrents vinculados={linked_count}"
        )

        if linked_count > 0:
            logger.info(f"🧹 [TVDB] Borrado en cascada activado: eliminando {linked_count} fichas torrent vinculadas a TVDB={tvdb_id}")
            session.exec(delete(TorrentCache).where(TorrentCache.tvdb_id == tvdb_id))

        session.exec(delete(TorrentTVDBCandidates).where(TorrentTVDBCandidates.tvdb_id == tvdb_id))
        session.exec(delete(TVDBEpisodes).where(TVDBEpisodes.tvdb_id == tvdb_id))

        session.delete(t)
        session.commit()

        logger.info(
            f"✅ [TVDB] Ficha maestra eliminada: TVDB={tvdb_id} | Torrents eliminados={linked_count}"
        )
        return {"success": True, "deleted_linked_torrents": linked_count}

# ------------------------------------------------------------
# Devuelve candidatos TVDB locales para que el usuario pueda enlazar
# una ficha sin hacer una búsqueda remota.
# ------------------------------------------------------------
@router.get("/tvdb/local_candidates")
async def get_local_candidates():
    with Session(engine) as session:
        shows = session.exec(select(TVDBCache)).all()
        return {"success": True, "results": shows}

# ------------------------------------------------------------
# Devuelve los candidatos TVDB guardados para una ficha torrent
# concreta durante procesos automáticos o manuales.
# ------------------------------------------------------------
@router.get("/torrent/{guid}/candidates")
async def get_torrent_candidates(guid: str):
    with Session(engine) as session:
        candidates = session.exec(select(TVDBCache).join(TorrentTVDBCandidates).where(TorrentTVDBCandidates.torrent_guid == guid)).all()
        return {"success": True, "results": candidates}

# ------------------------------------------------------------
# Busca series directamente en TheTVDB desde el panel para enlazar
# o descargar fichas maestras.
# ------------------------------------------------------------
@router.get("/tvdb/remote_search")
async def remote_search_tvdb(q: str):
    try:
        results = await search_tvdb(q, is_interactive=True)
        return {"success": True, "results": results}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ------------------------------------------------------------
# Descarga y guarda una ficha maestra completa de TheTVDB por su ID.
# ------------------------------------------------------------
@router.post("/tvdb/fetch_master/{tvdb_id}")
async def save_tvdb_master(tvdb_id: str):
    res = await fetch_full_tvdb_series(tvdb_id)
    return {"success": res}


# ------------------------------------------------------------
# Refresca manualmente una ficha maestra TVDB para actualizar datos
# de serie, temporadas y episodios en la biblioteca local.
# ------------------------------------------------------------
@router.post("/tvdb_cache/{tvdb_id}/refresh")
async def refresh_tvdb_cache_entry(tvdb_id: str):
    logger.info(f"🖱️ [TVDB] Actualización manual solicitada para la serie {tvdb_id}.")
    res = await fetch_full_tvdb_series(tvdb_id, await_episodes=True)
    if res:
        logger.info(f"✅ [TVDB] Actualización manual completada para la serie {tvdb_id}.")
    else:
        logger.warning(f"⚠️ [TVDB] La actualización manual no pudo completarse para la serie {tvdb_id}.")
    return {"success": res}

# ------------------------------------------------------------
# Vincula manualmente una ficha torrent con una serie TVDB y lanza
# la descarga de la ficha maestra en segundo plano.
# ------------------------------------------------------------
@router.post("/torrent/{guid}/link_tvdb")
async def manual_link_tvdb(guid: str, data: LinkTvdbForm):
    with Session(engine) as session:
        torrent = session.exec(select(TorrentCache).where(TorrentCache.guid == guid)).first()
        if not torrent: return {"success": False}
        
        torrent.tvdb_id = data.tvdb_id
        torrent.tvdb_status = "Listo"
        session.commit()

    asyncio.create_task(fetch_full_tvdb_series(data.tvdb_id))
    return {"success": True}
