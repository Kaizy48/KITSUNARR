from datetime import datetime, timezone
from typing import List, Optional
from sqlmodel import Field, Relationship, SQLModel


# ------------------------------------------------------------
# Devuelve el instante UTC actual sin tzinfo para columnas SQLite legacy.
# No recibe entradas ni tiene efectos/I/O, no lanza errores esperados y depende
# de datetime aware para evitar datetime.utcnow(), deprecado desde Python 3.12.
# ------------------------------------------------------------
def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------
# Devuelve el instante UTC actual como ISO naive para campos de texto legacy.
# No recibe entradas ni altera estado, no lanza errores esperados y depende de
# _utcnow_naive para compartir la convención temporal usada por SQLite.
# ------------------------------------------------------------
def _utcnow_naive_iso() -> str:
    return _utcnow_naive().isoformat()

# ------------------------------------------------------------
# Configuración maestra de Kitsunarr: administrador, API key propia,
# servicios externos, TheTVDB, Arr y qBittorrent.
# ------------------------------------------------------------
class SystemConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    admin_user: Optional[str] = Field(default=None)
    admin_password_hash: Optional[str] = Field(default=None)
    api_key: str = Field(unique=True)
    internal_url: Optional[str] = Field(default=None)
    tvdb_api_key: Optional[str] = Field(default=None)
    tvdb_token: Optional[str] = Field(default=None)
    tvdb_is_enabled: bool = Field(default=False)
    sonarr_url: Optional[str] = Field(default=None)
    sonarr_key: Optional[str] = Field(default=None)
    radarr_url: Optional[str] = Field(default=None)
    radarr_key: Optional[str] = Field(default=None)
    qbittorrent_url: Optional[str] = Field(default=None)
    qbittorrent_user: Optional[str] = Field(default=None)
    qbittorrent_password: Optional[str] = Field(default=None)

# ------------------------------------------------------------
# Configuración global del motor de IA que Kitsunarr usa para
# normalizar títulos, temporadas y metadatos de torrents.
# ------------------------------------------------------------
class AIConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    is_enabled: bool = Field(default=False)
    is_automated: bool = Field(default=False)
    provider: str = Field(default="ollama")
    model_name: str = Field(default="llama3")
    api_key: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(default="http://localhost:11434")
    custom_prompt: Optional[str] = Field(default=None)
    rpm_limit: int = Field(default=5)
    tpm_limit: int = Field(default=250000)
    rpd_limit: int = Field(default=20)


# ------------------------------------------------------------
# Estado persistente por modelo de IA para controlar límites,
# consumo y ventanas de cuota en Kitsunarr.
# ------------------------------------------------------------
class AIModel(SQLModel, table=True):
    model_key: str = Field(primary_key=True)
    provider: Optional[str] = None
    model_name: Optional[str] = None
    rpm_limit: int = Field(default=4)
    tpm_limit: int = Field(default=250000)
    rpd_limit: int = Field(default=20)

    minute_window_start: Optional[str] = None
    minute_requests: int = Field(default=0)
    minute_tokens: int = Field(default=0)

    daily_date: Optional[str] = None
    daily_count: int = Field(default=0)

# ------------------------------------------------------------
# Configuración de cada tracker/indexador disponible en Kitsunarr,
# incluyendo autenticación, estado y credenciales cifradas.
# ------------------------------------------------------------
class IndexerConfig(SQLModel, table=True):
    identifier: str = Field(primary_key=True)
    name: str
    is_enabled: bool = Field(default=True)
    auth_type: str = Field(default="cookie") 
    cookie_string: Optional[str] = Field(default=None)
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None)
    status: str = Field(default="ok") 

# ------------------------------------------------------------
# Tabla de relación entre una ficha torrent y los candidatos TVDB
# que Kitsunarr ha encontrado para ella.
# ------------------------------------------------------------
class TorrentTVDBCandidates(SQLModel, table=True):
    torrent_guid: str = Field(foreign_key="torrentcache.guid", primary_key=True)
    tvdb_id: str = Field(foreign_key="tvdbcache.tvdb_id", primary_key=True)

# ------------------------------------------------------------
# Episodios descargados desde TheTVDB para una serie maestra de la
# biblioteca local de Kitsunarr.
# ------------------------------------------------------------
class TVDBEpisodes(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tvdb_id: str = Field(foreign_key="tvdbcache.tvdb_id", index=True)
    episode_id: int = Field(unique=True)
    season_number: int
    episode_number: int
    name_es: Optional[str] = None
    name_original: Optional[str] = None
    overview_es: Optional[str] = None
    air_date: Optional[str] = None
    image_url: Optional[str] = None
    
    tvdb_show: Optional["TVDBCache"] = Relationship(back_populates="episodes")

# ------------------------------------------------------------
# Ficha maestra de una serie TheTVDB con nombres, sinopsis, pósters,
# temporadas y relaciones hacia torrents locales.
# ------------------------------------------------------------
class TVDBCache(SQLModel, table=True):
    tvdb_id: str = Field(primary_key=True)
    series_name_es: Optional[str] = None
    series_name_original: Optional[str] = None
    series_name_jp: Optional[str] = None
    aliases: Optional[str] = None
    overview_basic: Optional[str] = None
    overview_es: Optional[str] = None
    overview_original: Optional[str] = None
    poster_path: Optional[str] = None
    banner_path: Optional[str] = None
    first_aired: Optional[str] = None
    status: Optional[str] = None
    seasons_data: Optional[str] = None
    is_full_record: bool = False
    last_updated: str = Field(default_factory=_utcnow_naive_iso)
    
    torrents: List["TorrentCache"] = Relationship(back_populates="tvdb_series")
    candidates: List["TorrentCache"] = Relationship(back_populates="candidate_for", link_model=TorrentTVDBCandidates)
    
    episodes: List["TVDBEpisodes"] = Relationship(back_populates="tvdb_show")

# ------------------------------------------------------------
# Ficha local de torrent con snapshot remoto versionado, frescura, capturas y
# archivos. Separa overrides del usuario, swarm del tracker y telemetría viva
# de qBittorrent, además del enriquecimiento IA/TVDB usado por Sonarr.
# ------------------------------------------------------------
class TorrentCache(SQLModel, table=True):
    guid: str = Field(primary_key=True)
    source_guid: Optional[str] = Field(default=None, index=True)
    info_hash: Optional[str] = Field(default=None, index=True)
    tracker_title: Optional[str] = None
    fansub_name: Optional[str] = None
    original_title: str
    enriched_title: str
    description: Optional[str] = None
    description_override: Optional[str] = None
    poster_url: Optional[str] = None
    forum_url: Optional[str] = None
    screenshots_json: Optional[str] = None
    tracker_metadata_json: Optional[str] = None
    file_entries_json: Optional[str] = None
    size_bytes: Optional[int] = None
    indexer: Optional[str] = "unionfansub"
    download_url: Optional[str] = None
    published_at: Optional[datetime] = Field(default=None, index=True)
    first_seen_at: datetime = Field(default_factory=_utcnow_naive)
    last_scraped_at: Optional[datetime] = Field(default=None, index=True)
    scrape_revision: int = Field(default=0)
    tracker_freeleech: Optional[bool] = None
    freeleech_until: Optional[datetime] = None
    ai_translated_title: Optional[str] = None
    parsed_season: Optional[int] = Field(default=None, index=True)
    is_batch: bool = Field(default=False)
    raw_filenames: Optional[str] = None
    rename_mapping: Optional[str] = None
    tags: Optional[str] = None
    tags_override: Optional[str] = None
    tracker_seeders: Optional[int] = None
    tracker_leechers: Optional[int] = None
    
    ai_status: str = "Pendiente"
    tvdb_id: Optional[str] = Field(default=None, foreign_key="tvdbcache.tvdb_id", index=True)
    tvdb_status: str = Field(default="Pendiente")
    exists_in_client: bool = False
    client_status: Optional[str] = "unknown"
    progress: float = 0.0
    peers_seeds: int = 0
    peers_leechs: int = 0
    download_speed: int = 0
    upload_speed: int = 0
    eta: int = 8640000
    
    tvdb_series: Optional["TVDBCache"] = Relationship(back_populates="torrents")
    candidate_for: List["TVDBCache"] = Relationship(back_populates="candidates", link_model=TorrentTVDBCandidates)
