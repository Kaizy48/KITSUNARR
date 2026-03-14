# ==========================================
# MODELOS DE DATOS: CONFIGURACIÓN DEL SISTEMA E IA
# ==========================================
from typing import Optional
from sqlmodel import Field, SQLModel

"""
Modelo de base de datos que guarda la configuración global del núcleo de Kitsunarr.
Almacena la Clave API generada aleatoriamente que Sonarr necesita para comunicarse 
de forma segura, y las credenciales opcionales de terceros (como TheTVDB).

Atributos:
- id: Identificador único fijado a 1 (patrón Singleton).
- api_key: Clave de seguridad única para el protocolo Torznab.
- tvdb_api_key: Clave de acceso para la API v4 de TheTVDB.
- tvdb_token: Token de sesión persistente para evitar bloqueos por parte de TheTVDB.
- tvdb_is_enabled: Interruptor para activar o desactivar la integración con TheTVDB.
"""
class SystemConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    api_key: str = Field(unique=True)
    tvdb_api_key: Optional[str] = Field(default=None)
    tvdb_token: Optional[str] = Field(default=None)
    tvdb_is_enabled: bool = Field(default=False)

"""
Modelo de base de datos que guarda las credenciales y preferencias del Motor de Inteligencia Artificial.
Permite cambiar de proveedor (Gemini, OpenAI, Ollama) de forma dinámica sin necesidad de reiniciar.

Atributos:
- id: Identificador único fijado a 1 (patrón Singleton).
- is_enabled: Interruptor general para habilitar o deshabilitar cualquier función de IA.
- is_automated: Determina si el trabajador de fondo procesa los torrents automáticamente.
- provider: Proveedor de IA seleccionado ('gemini', 'openai' u 'ollama').
- model_name: Nombre del modelo específico a utilizar.
- api_key: Clave de autenticación para proveedores en la nube.
- base_url: URL base para la conexión con LLMs locales (como Ollama).
- custom_prompt: Prompt personalizado por el usuario para sustituir al predeterminado.
"""
class AIConfig(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    is_enabled: bool = False
    is_automated: bool = False
    provider: str = "gemini"
    model_name: str = "gemini-1.5-flash"
    api_key: str = ""
    base_url: str = "http://localhost:11434"
    custom_prompt: Optional[str] = Field(default=None)