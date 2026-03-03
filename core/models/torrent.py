# ==========================================
# MODELOS DE DATOS: CACHÉ DE TORRENTS Y TVDB
# ==========================================
from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime

class TorrentCache(SQLModel, table=True):
    """
    Representa un torrent extraído de un indexador y guardado en la base de datos local.
    Almacena tanto la información cruda como la procesada por la Inteligencia Artificial.
    """
    # --- IDENTIFICADORES BÁSICOS ---
    id: Optional[int] = Field(default=None, primary_key=True)
    guid: str = Field(index=True, unique=True) # ID único del torrent en el tracker original
    
    # --- METADATOS DEL TÍTULO ---
    original_title: str # El nombre tal y como aparece originalmente en el tracker
    enriched_title: str # El nombre tras el primer escaneo (se le añaden resoluciones y códecs de la ficha)
    ai_translated_title: Optional[str] = None # El título final perfectamente normalizado por la IA
    
    # --- INFORMACIÓN DE LA FICHA TÉCNICA ---
    description: Optional[str] = None # Sinopsis extraída de la página del torrent
    poster_url: Optional[str] = None # Enlace a la imagen de portada
    size_bytes: int = 0 # Tamaño estricto en bytes (requerido por el estándar Torznab)
    
    # --- METADATOS TÉCNICOS PARA DESCARGA ---
    indexer: Optional[str] = "unionfansub" # Rastrea de qué foro provino este archivo
    download_url: Optional[str] = None # URL directa de descarga
    pub_date: Optional[str] = None # Fecha de publicación en formato RFC (para Sonarr)
    freeleech_until: Optional[datetime] = None # Fecha en la que caduca el Freeleech (si aplica)
    
    # --- ESTADOS DEL PROCESAMIENTO ---
    # ai_status: Puede ser "Pendiente", "Listo", "Manual" (editado por el usuario) o "Error"
    ai_status: str = "Pendiente" 
    
    # --- FUTURA INTEGRACIÓN CON THETVDB ---
    tvdb_id: Optional[str] = Field(default=None, index=True) # ID oficial de la serie en TVDB
    tvdb_status: str = Field(default="Pendiente") # Estado de la validación cruzada con TVDB


class TVDBCache(SQLModel, table=True):
    """
    Almacena metadatos oficiales extraídos de la API/Web de TheTVDB.
    Se utiliza para no saturar el servicio externo y cruzar información con la tabla TorrentCache.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    search_query: str = Field(index=True) # El texto exacto que se usó para buscar en TVDB
    tvdb_id: str = Field(index=True, unique=True) # Identificador numérico oficial de TVDB
    
    series_title: str # Nombre canónico de la serie
    poster_path: Optional[str] = None # URL de la carátula oficial
    synopsis: Optional[str] = None # Resumen oficial de TVDB
    
    # Fecha de la última comprobación para saber cuándo los datos se quedan "viejos"
    last_updated: datetime = Field(default_factory=datetime.utcnow)