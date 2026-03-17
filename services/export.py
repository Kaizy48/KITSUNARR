# ==========================================
# MOTOR DE EXPORTACIÓN E IMPORTACIÓN RELACIONAL
# ==========================================
import json
from datetime import datetime
from fastapi.encoders import jsonable_encoder
from sqlmodel import Session, select

from core.logger import logger
from core.models.torrent import TorrentCache, TVDBCache, TVDBEpisodes, TorrentTVDBCandidates

# ==========================================
# FUNCIONES AUXILIARES DE LIMPIEZA Y REHIDRATACIÓN
# ==========================================

def safe_parse_datetime(date_str: str | None):
    """Convierte un string ISO 8601 de un JSON a un objeto datetime nativo de Python."""
    if not date_str:
        return None
    try:
        # Reemplazamos la Z si viene de Javascript para compatibilidad ISO
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None

def sanitize_torrent_for_export(torrent: TorrentCache) -> dict:
    """
    Convierte el modelo a diccionario y elimina datos locales o sensibles.
    """
    data = jsonable_encoder(torrent)
    data.pop("download_url", None) 
    return data

def rehydrate_torrent_data(t_data: dict, base_url: str) -> dict:
    """
    Reconstruye los campos dinámicos y los tipos de dato (como fechas)
    antes de insertar en la nueva base de datos.
    """
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

def export_torrents_only(session: Session) -> dict:
    """Exporta solo la caché de torrents y sus candidatos (Crowdsourcing IA)."""
    torrents = session.exec(select(TorrentCache)).all()
    candidates = session.exec(select(TorrentTVDBCandidates)).all()
    
    return {
        "type": "torrents_only",
        "torrents": [sanitize_torrent_for_export(t) for t in torrents],
        "candidates": jsonable_encoder(candidates)
    }

def export_tvdb_only(session: Session) -> dict:
    """Exporta solo la base de conocimientos oficial (Ideal para compartir)."""
    tvdb_shows = session.exec(select(TVDBCache).where(TVDBCache.is_full_record == True)).all()
    tvdb_episodes = session.exec(select(TVDBEpisodes)).all()
    
    return {
        "type": "tvdb_only",
        "tvdb_cache": jsonable_encoder(tvdb_shows),
        "tvdb_episodes": jsonable_encoder(tvdb_episodes)
    }

def export_full_bundle(session: Session) -> dict:
    """Exporta TODA la base de datos, pero solo los torrents verificados con éxito (Tick Verde)."""
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

def import_relational_data(data: dict, session: Session, base_url: str) -> dict:
    """
    Importa los datos respetando el orden de las Foreign Keys para no romper SQLite.
    Retorna una lista de tvdb_ids huérfanos para que el main.py inicie su descarga.
    """
    imported_counts = {"torrents": 0, "tvdb": 0, "episodes": 0, "candidates": 0}
    missing_tvdb_ids = set()
    
    # 1. Importar Fichas Maestras (TVDBCache) - Nivel 0 Relacional
    if "tvdb_cache" in data:
        for show_data in data["tvdb_cache"]:
            existing = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == show_data["tvdb_id"])).first()
            if not existing:
                # Arreglo Fecha: String -> Datetime
                show_data["last_updated"] = safe_parse_datetime(show_data.get("last_updated")) or datetime.utcnow()
                
                new_show = TVDBCache(**show_data)
                session.add(new_show)
                imported_counts["tvdb"] += 1
        session.commit()
        
    # 2. Importar Episodios (TVDBEpisodes) - Depende de Nivel 0
    if "tvdb_episodes" in data:
        for ep_data in data["tvdb_episodes"]:
            parent_exists = session.exec(select(TVDBCache).where(TVDBCache.tvdb_id == ep_data["tvdb_id"])).first()
            if parent_exists:
                ep_data.pop("id", None) 
                new_ep = TVDBEpisodes(**ep_data)
                session.add(new_ep)
                imported_counts["episodes"] += 1
        session.commit()
        
    # 3. Importar Torrents (TorrentCache) - Pueden depender de Nivel 0
    if "torrents" in data:
        for t_data in data["torrents"]:
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
        
    # 4. Importar Tabla Puente (TorrentTVDBCandidates) - Depende de Nivel 0 y Nivel 3
    if "candidates" in data:
        for cand_data in data["candidates"]:
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

    logger.info(f"📦 Importación finalizada: {imported_counts}")
    return {"counts": imported_counts, "missing_tvdb_ids": list(missing_tvdb_ids)}