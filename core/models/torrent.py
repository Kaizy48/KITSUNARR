# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


# ==========================================
# MODELOS DE DATOS: CACHÉ DE TORRENTS Y TVDB
# ==========================================

# ==========================================
# 1. TABLA INTERMEDIA (RELACIÓN N:M)
# ==========================================
"""
Tabla de unión para la relación "Muchos a Muchos" entre Torrents y Fichas TVDB.
Permite que un torrent tenga múltiples "candidatos" iniciales sin duplicar datos,
y que una ficha de TVDB pueda ser candidata para muchos torrents distintos.
"""
class TorrentTVDBCandidates(SQLModel, table=True):
    torrent_guid: str = Field(foreign_key="torrentcache.guid", primary_key=True)
    tvdb_id: str = Field(foreign_key="tvdbcache.tvdb_id", primary_key=True)


# ==========================================
# 2. ESTRUCTURA DE EPISODIOS (TVDB)
# ==========================================
"""
Almacena los episodios individuales de una serie de TVDB.
"""
class TVDBEpisodes(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tvdb_id: str = Field(foreign_key="tvdbcache.tvdb_id", index=True)
    season_number: int
    episode_number: int
    name_es: Optional[str] = None
    name_en: Optional[str] = None
    air_date: Optional[str] = None
    
    series: Optional["TVDBCache"] = Relationship(back_populates="episodes")


# ==========================================
# 3. BIBLIOTECA MAESTRA DE THETVDB
# ==========================================
"""
Enciclopedia local de metadatos. Ahora soporta dos estados:
- is_full_record = False (Ficha Básica): Creada al buscar candidatos (Nombre, Año, Sinopsis Básica).
- is_full_record = True (Ficha Maestra): Actualizada al hacer Match (Temporadas, Episodios, HQ Poster).
"""
class TVDBCache(SQLModel, table=True):
    tvdb_id: str = Field(primary_key=True)
    series_name_es: str
    series_name_en: Optional[str] = None
    aliases: Optional[str] = None 
    
    # Textos Descriptivos
    overview_basic: Optional[str] = None # Sinopsis corta de la búsqueda (Para ayudar a la IA)
    overview_es: Optional[str] = None # Sinopsis completa oficial
    overview_en: Optional[str] = None
    
    # Imágenes
    poster_path: Optional[str] = None
    banner_path: Optional[str] = None
    
    # Metadatos
    status: Optional[str] = None
    first_aired: Optional[str] = None
    seasons_data: Optional[str] = None 
    
    # Control de Frescura y Estado
    is_full_record: bool = Field(default=False)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    # ================= Relaciones =================
    # 1 a Muchos: Una serie tiene muchos episodios
    episodes: List[TVDBEpisodes] = Relationship(back_populates="series", cascade_delete=True)
    # 1 a Muchos: Una serie puede ser el resultado final (match) de muchos torrents
    torrents: List["TorrentCache"] = Relationship(back_populates="tvdb_series")
    # Muchos a Muchos: Esta serie es "candidata" para varios torrents no resueltos
    candidate_for: List["TorrentCache"] = Relationship(back_populates="candidates", link_model=TorrentTVDBCandidates)


# ==========================================
# 4. CACHÉ DE TORRENTS Y ESTADO DEL CLIENTE
# ==========================================
"""
Almacena la información extraída de los trackers y el estado de su 
procesamiento por la IA y TheTVDB, así como el progreso de descarga.
"""
"""
Almacena la información extraída de los trackers y el estado de su 
procesamiento por la IA y TheTVDB, así como el progreso de descarga y telemetría.
"""
class TorrentCache(SQLModel, table=True):
    guid: str = Field(primary_key=True)
    info_hash: Optional[str] = Field(default=None, index=True)
    
    # Metadatos del Tracker
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
    
    # Procesamiento IA y TVDB
    ai_translated_title: Optional[str] = None
    ai_status: str = "Pendiente"
    
    # Clave Foránea apuntando al ID final de la serie
    tvdb_id: Optional[str] = Field(default=None, foreign_key="tvdbcache.tvdb_id", index=True)
    tvdb_status: str = Field(default="Pendiente")

    # ==================================================
    # Estado del Cliente de Descargas (Telemetría qBittorrent)
    # ==================================================
    exists_in_client: bool = False
    client_status: Optional[str] = "unknown"
    progress: float = 0.0
    
    # Rendimiento y Conectividad
    peers_seeds: int = 0
    peers_leechs: int = 0
    download_speed: int = 0
    upload_speed: int = 0
    eta: int = 0
    ratio: float = 0.0
    
    added_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ================= Relaciones =================
    # Relación final: El anime al que pertenece oficialmente
    tvdb_series: Optional[TVDBCache] = Relationship(back_populates="torrents")
    # Relación temporal: Los posibles animes que la IA debe evaluar
    candidates: List[TVDBCache] = Relationship(back_populates="candidate_for", link_model=TorrentTVDBCandidates)