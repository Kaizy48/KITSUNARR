# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
from typing import Optional
from sqlmodel import Field, SQLModel

# ==========================================
# MODELOS DE DATOS: CONFIGURACIÓN DEL SISTEMA E IA
# ==========================================

"""
Modelo de base de datos que guarda la configuración global del núcleo de Kitsunarr.
Almacena credenciales críticas del sistema y enlaces con aplicaciones de terceros.

Atributos de Seguridad:
- admin_user: Nombre de usuario para el acceso a la interfaz web.
- admin_password_hash: Hash irreversible (Argon2) de la contraseña del administrador.

Atributos de Integración:
- id: Identificador único fijado a 1 (patrón Singleton).
- api_key: Clave de seguridad única generada para el protocolo Torznab.
- internal_url: URL interna para la comunicación entre servicios.
- tvdb_api_key: (Cifrado) Clave de acceso para la API v4 de TheTVDB.
- tvdb_token: (Cifrado) Token de sesión JWT para TheTVDB.
- tvdb_is_enabled: Interruptor para activar la integración con TheTVDB.
- sonarr_url: URL base de la instancia de Sonarr.
- sonarr_key: (Cifrado) Clave API de la instancia de Sonarr.
- radarr_url: URL base de la instancia de Radarr.
- radarr_key: (Cifrado) Clave API de la instancia de Radarr.
- qbittorrent_url: URL base del cliente qBittorrent.
- qbittorrent_user: Usuario de acceso a la API de qBittorrent.
- qbittorrent_password: (Cifrado) Contraseña de acceso a qBittorrent.
"""
class SystemConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    
    # Credenciales de Administrador
    admin_user: Optional[str] = Field(default=None)
    admin_password_hash: Optional[str] = Field(default=None)
    
    # Configuración del Sistema y TVDB
    api_key: str = Field(unique=True)
    internal_url: Optional[str] = Field(default=None)
    tvdb_api_key: Optional[str] = Field(default=None)
    tvdb_token: Optional[str] = Field(default=None)
    tvdb_is_enabled: bool = Field(default=False)
    
    # Integraciones ARR
    sonarr_url: Optional[str] = Field(default=None)
    sonarr_key: Optional[str] = Field(default=None)
    radarr_url: Optional[str] = Field(default=None)
    radarr_key: Optional[str] = Field(default=None)
    
    # Integración de Cliente de Descarga
    qbittorrent_url: Optional[str] = Field(default=None)
    qbittorrent_user: Optional[str] = Field(default=None)
    qbittorrent_password: Optional[str] = Field(default=None)


"""
Modelo de base de datos que guarda las credenciales y preferencias del Motor de Inteligencia Artificial.
Permite cambiar de proveedor (Gemini, OpenAI, Ollama) de forma dinámica sin necesidad de reiniciar.

Atributos:
- id: Identificador único fijado a 1 (patrón Singleton).
- is_enabled: Interruptor general para habilitar la IA.
- is_automated: Determina si el trabajador de fondo procesa automáticamente.
- provider: Proveedor seleccionado ('gemini', 'openai' u 'ollama').
- model_name: Nombre del modelo específico a utilizar.
- api_key: (Cifrado) Clave de autenticación para proveedores en la nube.
- base_url: URL base para la conexión con LLMs locales.
- custom_prompt: Prompt personalizado por el usuario.
- rpm_limit: Límite de peticiones por minuto.
- tpm_limit: Límite de tokens por minuto.
- rpd_limit: Límite de peticiones por día.
"""
class AIConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    is_enabled: bool = False
    is_automated: bool = False
    provider: str = "gemini"
    model_name: str = "gemini-2.5-flash"
    api_key: str = ""
    base_url: str = "http://localhost:11434"
    custom_prompt: Optional[str] = Field(default=None)
    rpm_limit: int = Field(default=5)
    tpm_limit: int = Field(default=250000)
    rpd_limit: int = Field(default=20)