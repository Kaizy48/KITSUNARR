# ==========================================
# MODELOS DE DATOS: CACHÉ DE TORRENTS Y TVDB
# ==========================================
from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime

# ==========================================
# 📚 BIBLIOTECA MAESTRA DE THETVDB
# ==========================================

"""
Modelo de base de datos para almacenar la 'Ficha Maestra' oficial de una serie.
Centraliza toda la información de TheTVDB para evitar consultas repetitivas a la API
y permitir que el sistema funcione de forma autónoma (offline) para series conocidas.

Atributos:
- tvdb_id: Identificador numérico oficial y único de TheTVDB (Clave Primaria).
- series_name_es: Nombre oficial de la serie en español.
- series_name_en: Nombre oficial de la serie en inglés (para compatibilidad con Sonarr).
- aliases: Lista de nombres alternativos (Japonés, abreviaturas, etc.) en formato JSON.
- overview_es: Sinopsis o resumen oficial en español.
- overview_en: Sinopsis o resumen oficial en inglés.
- poster_path: URL de la carátula o póster oficial en alta calidad.
- banner_path: URL del banner o imagen de fondo oficial.
- status: Estado de producción de la serie (ej: 'Continuing', 'Ended').
- first_aired: Fecha o año del primer estreno mundial.
- seasons_data: JSON estructurado con temporadas, número de episodios y sus nombres si están disponibles.
- last_updated: Fecha de la última vez que se sincronizó esta ficha con la API de TVDB.
"""
class TVDBCache(SQLModel, table=True):
    tvdb_id: str = Field(primary_key=True)
    series_name_es: str
    series_name_en: Optional[str] = None
    aliases: Optional[str] = None 
    
    overview_es: Optional[str] = None
    overview_en: Optional[str] = None
    poster_path: Optional[str] = None
    banner_path: Optional[str] = None
    
    status: Optional[str] = None
    first_aired: Optional[str] = None
    
    seasons_data: Optional[str] = None 
    
    last_updated: datetime = Field(default_factory=datetime.utcnow)


# ==========================================
# 📥 CACHÉ DE TORRENTS Y ESTADO DEL CLIENTE
# ==========================================

"""
Modelo de base de datos que representa el ciclo de vida completo de un torrent.
Une la información extraída del indexador, el procesamiento de la IA/TVDB
y el estado actual dentro del cliente de descargas (qBittorrent/Transmission).

Atributos:
- guid: Identificador único del tracker original (Clave Primaria).
- info_hash: Hash único del torrent para sincronizar con el cliente.
- original_title: Título original tal cual aparece en el tracker.
- enriched_title: Título tras el raspado de metadatos técnicos del indexador.
- ai_translated_title: Título normalizado por la IA para el consumo de Sonarr.
- description: Sinopsis original proporcionada por el uploader en el tracker.
- poster_url: URL de la imagen promocional que aparece en el tracker.
- size_bytes: Tamaño del archivo en bytes (requerido por Torznab).
- indexer: Nombre del indexador de origen (ej: 'unionfansub').
- download_url: URL de descarga (o proxy interno) del archivo .torrent.
- pub_date: Fecha de publicación original en formato RFC para Sonarr.
- freeleech_until: Fecha de caducidad si el torrent tiene estado de descarga gratuita.
- ai_status: Estado del motor de IA ('Pendiente', 'Listo', 'Manual', 'Error').
- tvdb_id: ID oficial vinculado que conecta con la tabla TVDBCache.
- tvdb_status: Estado de la vinculación ('Pendiente', 'Candidatos', 'Listo', 'Error').
- tvdb_candidates: Almacenamiento JSON temporal de candidatos (se limpia al validar).

- client_status: Estado en el cliente ('downloading', 'seeding', 'stalled', 'error').
- peers_seeds: Número de semillas actuales reportadas por el cliente.
- peers_leechs: Número de usuarios descargando actualmente.
- progress: Porcentaje de progreso de la descarga (0.0 a 100.0).
- exists_in_client: Booleano que indica si el torrent está cargado en el cliente.
"""
class TorrentCache(SQLModel, table=True):
    guid: str = Field(primary_key=True)
    info_hash: Optional[str] = Field(default=None, index=True)
    
    fansub_name: Optional[str] = None
    original_title: str
    enriched_title: str
    description: Optional[str] = None
    poster_url: Optional[str] = None
    size_bytes: int = 0
    indexer: Optional[str] = "unionfansub"
    download_url: Optional[str] = None
    pub_date: Optional[str] = None
    freeleech_until: Optional[datetime] = None
    
    ai_translated_title: Optional[str] = None
    ai_status: str = "Pendiente"
    tvdb_id: Optional[str] = Field(default=None, index=True)
    tvdb_status: str = Field(default="Pendiente")
    tvdb_candidates: Optional[str] = None 

    client_status: Optional[str] = "unknown"
    peers_seeds: int = 0
    peers_leechs: int = 0
    progress: float = 0.0
    exists_in_client: bool = False
    
    added_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)