# ==========================================
# MODELOS DE DATOS: CONFIGURACIÓN DE TRACKERS
# ==========================================
from typing import Optional
from sqlmodel import Field, SQLModel

class IndexerConfig(SQLModel, table=True):
    """
    Guarda las credenciales y el estado de conexión de los diferentes indexadores
    (trackers) configurados por el usuario.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # --- IDENTIDAD DEL INDEXADOR ---
    name: str = Field(unique=True)           # Nombre visual
    identifier: str = Field(unique=True)     # Nombre interno para lógica del código
    
    # --- CREDENCIALES DE AUTENTICACIÓN ---
    # auth_type: Indica el método usado para burlar la seguridad ("cookie", "login" o "api")
    auth_type: str                           
    cookie_string: Optional[str] = None      # La cookie de sesión copiada por el usuario del navegador
    username: Optional[str] = None           # (Usuario de auto-login)
    password: Optional[str] = None           # (Contraseña de auto-login)
    api_key: Optional[str] = None            # (Si el tracker soporta APIs oficiales)
    
    # --- ESTADO Y CONTROL ---
    is_enabled: bool = True                  # Interruptor para apagar el uso del tracker sin borrar credenciales
    status: str = "unknown"                  # Muestra el resultado del último ping ("ok", "error", "unknown")