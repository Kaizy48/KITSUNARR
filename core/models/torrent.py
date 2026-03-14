# ==========================================
# MODELOS DE DATOS: CACHÉ DE TORRENTS Y TVDB
# ==========================================
from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime

"""
Modelo de base de datos que representa un torrent extraído de un indexador y guardado localmente.
Almacena tanto la información original en crudo como los metadatos procesados y traducidos por la IA.

Atributos:
- guid: Identificador único del tracker original (Clave Primaria).
- original_title: Nombre del torrent tal y como aparece en el tracker.
- enriched_title: Nombre del torrent tras un escaneo básico para extraer calidad y códecs.
- ai_translated_title: Título final perfectamente normalizado por la IA.
- description: Sinopsis extraída directamente de la página del torrent.
- poster_url: Enlace a la imagen de portada o banner del anime.
- size_bytes: Tamaño exacto del archivo en bytes, requerido por el protocolo Torznab.
- indexer: Nombre del indexador de origen (por defecto 'unionfansub').
- download_url: Enlace directo o de proxy para la descarga del archivo .torrent.
- pub_date: Fecha de publicación en formato RFC estándar para Sonarr.
- freeleech_until: Fecha límite de caducidad si el torrent tiene estado Freeleech.
- ai_status: Estado actual del procesamiento de IA ('Pendiente', 'Listo', 'Manual', 'Error').
- tvdb_id: Identificador numérico oficial de la serie en la base de datos de TheTVDB.
- tvdb_status: Estado de la vinculación con TVDB ('Pendiente', 'Candidatos', 'Listo', 'Error', 'No Encontrado').
- tvdb_candidates: Almacenamiento en JSON de las posibles coincidencias devueltas por TheTVDB.
"""
class TorrentCache(SQLModel, table=True):
    guid: str = Field(primary_key=True)
    original_title: str
    enriched_title: str
    ai_translated_title: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    size_bytes: int = 0
    indexer: Optional[str] = "unionfansub"
    download_url: Optional[str] = None
    pub_date: Optional[str] = None
    freeleech_until: Optional[datetime] = None
    ai_status: str = "Pendiente"
    tvdb_id: Optional[str] = Field(default=None, index=True)
    tvdb_status: str = Field(default="Pendiente")
    tvdb_candidates: Optional[str] = None

"""
Modelo de base de datos para almacenar metadatos oficiales cacheados desde TheTVDB.
Se utiliza para evitar saturar la API externa y permitir cruzar información validada con los torrents.

Atributos:
- tvdb_id: Identificador numérico oficial y único de TheTVDB (Clave Primaria).
- search_query: Cadena de texto exacta que se utilizó para realizar la búsqueda original.
- series_title: Nombre canónico y oficial de la serie devuelto por TheTVDB.
- poster_path: URL de la carátula oficial en alta calidad.
- synopsis: Resumen oficial u overview proporcionado por TheTVDB.
- last_updated: Fecha de la última actualización de estos metadatos en la base de datos local.
"""
class TVDBCache(SQLModel, table=True):
    tvdb_id: str = Field(primary_key=True)
    search_query: str = Field(index=True)
    series_title: str
    poster_path: Optional[str] = None
    synopsis: Optional[str] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)