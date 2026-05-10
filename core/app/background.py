import asyncio
from core.app.logger import logger
from sqlmodel import Session, select, or_, and_

from core.database.engine import engine
from core.database.models import AIConfig, SystemConfig, TorrentCache, TorrentTVDBCandidates
from services.ai.engine import process_pending_torrents
from services.tvdb.tvdb_api import process_pending_tvdb

_worker_wake_event = asyncio.Event()
_worker_pause_reason: str | None = None


# ------------------------------------------------------------
# Despierta el worker de fondo de Kitsunarr cuando una búsqueda,
# cambio de configuración o evento externo puede haber dejado
# fichas pendientes de TVDB o IA.
# ------------------------------------------------------------
def wake_worker(reason: str = "evento externo"):
    if not _worker_wake_event.is_set():
        logger.info(f"🔔 [WORKER] Despertando worker: {reason}.")
    _worker_wake_event.set()


# ------------------------------------------------------------
# Pausa temporalmente el worker de fondo para que operaciones grandes
# de mantenimiento no compitan con TVDB o IA escribiendo en la base.
# ------------------------------------------------------------
def pause_worker(reason: str = "mantenimiento"):
    global _worker_pause_reason
    _worker_pause_reason = reason
    logger.info(f"⏸️ [WORKER] Pausa temporal solicitada: {reason}.")


# ------------------------------------------------------------
# Reactiva el worker de fondo tras una operacion de mantenimiento y
# lo despierta por si han quedado fichas pendientes.
# ------------------------------------------------------------
def resume_worker(reason: str = "mantenimiento completado"):
    global _worker_pause_reason
    if _worker_pause_reason:
        logger.info(f"▶️ [WORKER] Pausa temporal finalizada: {reason}.")
    _worker_pause_reason = None
    wake_worker(reason)


# ------------------------------------------------------------
# Indica si Kitsunarr esta en una pausa de mantenimiento para que
# Torznab pueda evitar escrituras de cache durante importaciones.
# ------------------------------------------------------------
def is_worker_paused() -> bool:
    return bool(_worker_pause_reason)


# ------------------------------------------------------------
# Comprueba si existen fichas de torrent pendientes de identificación
# con TheTVDB y si el servicio TVDB está habilitado en Kitsunarr.
# ------------------------------------------------------------
def _has_pending_tvdb_work(session: Session) -> bool:
    config = session.exec(select(SystemConfig)).first()
    if not config or not config.tvdb_api_key or not config.tvdb_is_enabled:
        return False

    candidate = session.exec(
        select(TorrentCache.guid)
        .where(TorrentCache.tvdb_id == None)
        .where(
            or_(
                TorrentCache.tvdb_status == "Pendiente",
                and_(TorrentCache.tvdb_status == "No Encontrado", TorrentCache.ai_status == "Listo")
            )
        )
        .limit(1)
    ).first()
    return bool(candidate)


# ------------------------------------------------------------
# Comprueba si existen fichas listas para normalización con IA según
# el estado del motor, el modo automático y la vinculación TVDB.
# ------------------------------------------------------------
def _has_pending_ai_work(session: Session) -> bool:
    ai_config = session.exec(select(AIConfig)).first()
    if not ai_config or not ai_config.is_enabled or not ai_config.is_automated:
        return False

    config = session.exec(select(SystemConfig)).first()
    tvdb_worker_enabled = bool(config and config.tvdb_is_enabled and config.tvdb_api_key)

    query = select(TorrentCache.guid).where(
        or_(TorrentCache.ai_status == "Pendiente", TorrentCache.ai_status == "Error")
    )
    if tvdb_worker_enabled:
        ready_linked = session.exec(
            query.where(TorrentCache.tvdb_status == "Listo").limit(1)
        ).first()
        if ready_linked:
            return True

        ready_candidates = session.exec(
            select(TorrentTVDBCandidates.torrent_guid)
            .join(TorrentCache, TorrentCache.guid == TorrentTVDBCandidates.torrent_guid)
            .where(or_(TorrentCache.ai_status == "Pendiente", TorrentCache.ai_status == "Error"))
            .where(TorrentCache.tvdb_status == "Candidatos")
            .limit(1)
        ).first()
        return bool(ready_candidates)

    candidate = session.exec(query.limit(1)).first()
    return bool(candidate)


# ------------------------------------------------------------
# Resume en una sola consulta de estado si Kitsunarr tiene trabajo
# pendiente para los flujos de TVDB o IA.
# ------------------------------------------------------------
def _has_pending_work() -> tuple[bool, bool]:
    with Session(engine) as session:
        pending_tvdb = _has_pending_tvdb_work(session)
        pending_ai = _has_pending_ai_work(session)
    return pending_tvdb, pending_ai


# ------------------------------------------------------------
# Bucle principal del worker de Kitsunarr. Mantiene en marcha el
# procesado automático de TVDB e IA y entra en espera cuando no hay
# fichas pendientes.
# ------------------------------------------------------------
async def worker_loop():
    logger.info("⚙️ Motor de tareas en segundo plano iniciado.")
    wake_worker("arranque de la aplicación")
    is_sleeping = False

    while True:
        try:
            if _worker_pause_reason:
                await asyncio.sleep(0.5)
                continue

            pending_tvdb, pending_ai = _has_pending_work()

            if not pending_tvdb and not pending_ai:
                if not is_sleeping:
                    logger.info("😴 [WORKER] Sin fichas pendientes. Worker en espera hasta nuevo evento.")
                    is_sleeping = True
                await _worker_wake_event.wait()
                _worker_wake_event.clear()
                if is_sleeping:
                    logger.info("🌅 [WORKER] Worker reactivado tras señal de despertar.")
                    is_sleeping = False
                continue

            if is_sleeping:
                logger.info("🌅 [WORKER] Trabajo detectado. Saliendo de modo espera.")
                is_sleeping = False

            if pending_tvdb:
                await process_pending_tvdb()

            if pending_ai:
                await process_pending_torrents()

            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info("🛑 [WORKER] Worker de fondo detenido.")
            raise
        except Exception as e:
            logger.error(f"❌ Error crítico en el bucle del trabajador: {e}")
            await asyncio.sleep(2)
