# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import json
import asyncio
from datetime import datetime

from core.database import engine
from fastapi.encoders import jsonable_encoder
from sqlmodel import Session, select

from core.logger import logger
from core.models.torrent import TorrentCache, TVDBCache, TVDBEpisodes, TorrentTVDBCandidates


# ==========================================
# FUNCIONES AUXILIARES DE LIMPIEZA Y REHIDRATACIÓN
# ==========================================

"""
Convierte un string ISO 8601 de un JSON a un objeto datetime nativo de Python.
Reemplaza la 'Z' (común en Javascript) para mantener la compatibilidad ISO.
"""
def safe_parse_datetime(date_str: str | None):
    if not date_str:
        return None
    try:
        # Reemplazamos la Z si viene de Javascript para compatibilidad ISO
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None

"""
Convierte el modelo de base de datos a un diccionario serializable 
y elimina datos locales o sensibles (como la URL de descarga del proxy).
"""
def sanitize_torrent_for_export(torrent: TorrentCache) -> dict:
    data = jsonable_encoder(torrent)
    data.pop("download_url", None) 
    return data

"""
Reconstruye los campos dinámicos y parsea los tipos de dato correctos 
(como las fechas) antes de insertar un torrent en la nueva base de datos.
"""
def rehydrate_torrent_data(t_data: dict, base_url: str) -> dict:
    guid = t_data.get("guid", "")
    t_data["download_url"] = f"{base_url}/api/download/{guid}_base"
    
    # Conversión obligatoria de Strings a objetos Datetime
    t_data["added_at"] = safe_parse_datetime(t_data.get("added_at")) or datetime.utcnow()
    t_data["updated_at"] = safe_parse_datetime(t_data.get("updated_at")) or datetime.utcnow()
    t_data["freeleech_until"] = safe_parse_datetime(t_data.get("freeleech_until"))
    
    return t_data


# ==========================================
# MÓDULOS DE EXPORTACIÓN
# ==========================================

"""
Exporta únicamente la caché de torrents y sus candidatos asociados.
Ideal para copias de seguridad rápidas o crowdsourcing de entrenamiento de IA.
"""
def export_torrents_only(session: Session) -> dict:
    torrents = session.exec(select(TorrentCache)).all()
    candidates = session.exec(select(TorrentTVDBCandidates)).all()
    
    return {
        "type": "torrents_only",
        "torrents": [sanitize_torrent_for_export(t) for t in torrents],
        "candidates": jsonable_encoder(candidates)
    }

"""
Exporta en exclusiva la base de conocimientos oficial de TheTVDB 
(Fichas Maestras y Episodios). Útil para compartir metadatos estructurados.
"""
def export_tvdb_only(session: Session) -> dict:
    tvdb_shows = session.exec(select(TVDBCache).where(TVDBCache.is_full_record == True)).all()
    tvdb_episodes = session.exec(select(TVDBEpisodes)).all()
    
    return {
        "type": "tvdb_only",
        "tvdb_cache": jsonable_encoder(tvdb_shows),
        "tvdb_episodes": jsonable_encoder(tvdb_episodes)
    }

"""
Exporta TODA la base de datos relacional en un único archivo (Bundle), 
pero limitándose a los torrents que ya han sido verificados con éxito (Tick Verde).
"""
def export_full_bundle(session: Session) -> dict:
    torrents = session.exec(select(TorrentCache).where(TorrentCache.tvdb_status == "Listo")).all()
    tvdb_shows = session.exec(select(TVDBCache).where(TVDBCache.is_full_record == True)).all()
    tvdb_episodes = session.exec(select(TVDBEpisodes)).all()
    candidates = session.exec(select(TorrentTVDBCandidates)).all()
    
    return {
        "type": "full_bundle",
        "torrents": [sanitize_torrent_for_export(t) for t in torrents],
        "tvdb_cache": jsonable_encoder(tvdb_shows),
        "tvdb_episodes": jsonable_encoder(tvdb_episodes),
        "candidates": jsonable_encoder(candidates)
    }


# ==========================================
# MOTOR DE IMPORTACIÓN INTELIGENTE
# ==========================================

"""
Importa los datos JSON en la base local respetando el orden de las Foreign Keys 
para no romper la integridad de SQLite. Utiliza transacciones granulares 
(batch commits) para no bloquear la BD durante importaciones masivas.
"""
async def import_relational_data(data: dict, base_url: str) -> dict:
    imported_counts = {"torrents": 0, "tvdb": 0, "episodes": 0, "candidates": 0}
    missing_tvdb_ids = set()
    BATCH_SIZE = 100
    
    if "tvdb_cache" in data:
        for i in range(0, len(data["tvdb_cache"]), BATCH_SIZE):
            batch = data["tvdb_cache"][i:i+BATCH_SIZE]
            with Session(engine) as session:
                for show_data in batch:
                    existing = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == show_data["tvdb_id"])).first()
                    if not existing:
                        show_data["last_updated"] = safe_parse_datetime(show_data.get("last_updated")) or datetime.utcnow()
                        new_show = TVDBCache(**show_data)
                        session.add(new_show)
                        imported_counts["tvdb"] += 1
                session.commit()
            await asyncio.sleep(0.01)
        
    if "tvdb_episodes" in data:
        for i in range(0, len(data["tvdb_episodes"]), BATCH_SIZE):
            batch = data["tvdb_episodes"][i:i+BATCH_SIZE]
            with Session(engine) as session:
                for ep_data in batch:
                    parent_exists = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == ep_data["tvdb_id"])).first()
                    if parent_exists:
                        ep_data.pop("id", None) 
                        new_ep = TVDBEpisodes(**ep_data)
                        session.add(new_ep)
                        imported_counts["episodes"] += 1
                session.commit()
            await asyncio.sleep(0.01)
        
    if "torrents" in data:
        for i in range(0, len(data["torrents"]), BATCH_SIZE):
            batch = data["torrents"][i:i+BATCH_SIZE]
            with Session(engine) as session:
                for t_data in batch:
                    existing = session.exec(select(TorrentCache).where(TorrentCache.guid == t_data["guid"])).first()
                    if not existing:
                        clean_t_data = rehydrate_torrent_data(t_data, base_url)
                        tvdb_id = clean_t_data.get("tvdb_id")
                        if tvdb_id:
                            show_exists = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == tvdb_id)).first()
                            if not show_exists:
                                clean_t_data["tvdb_status"] = "Pendiente"
                                missing_tvdb_ids.add(tvdb_id)
                        
                        new_t = TorrentCache(**clean_t_data)
                        session.add(new_t)
                        imported_counts["torrents"] += 1
                session.commit()
            await asyncio.sleep(0.01)
        
    if "candidates" in data:
        for i in range(0, len(data["candidates"]), BATCH_SIZE):
            batch = data["candidates"][i:i+BATCH_SIZE]
            with Session(engine) as session:
                for cand_data in batch:
                    t_exists = session.exec(select(TorrentCache).where(TorrentCache.guid == cand_data["torrent_guid"])).first()
                    show_exists = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == cand_data["tvdb_id"])).first()
                    
                    if t_exists and show_exists:
                        link_exists = session.exec(select(TorrentTVDBCandidates).where(
                            TorrentTVDBCandidates.torrent_guid == cand_data["torrent_guid"],
                            TorrentTVDBCandidates.tvdb_id == cand_data["tvdb_id"]
                        )).first()
                        if not link_exists:
                            new_link = TorrentTVDBCandidates(**cand_data)
                            session.add(new_link)
                            imported_counts["candidates"] += 1
                session.commit()
            await asyncio.sleep(0.01)

    logger.info(f"📦 Importación finalizada: {imported_counts}")
    return {"counts": imported_counts, "missing_tvdb_ids": list(missing_tvdb_ids)}