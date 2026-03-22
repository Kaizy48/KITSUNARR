# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
from typing import Optional
from sqlmodel import Field, SQLModel

# ==========================================
# MODELOS DE DATOS: CONFIGURACIÓN DE TRACKERS
# ==========================================

"""
Modelo de base de datos que guarda las credenciales y el estado de conexión de los diferentes indexadores.
Controla la autenticación de cada tracker configurado por el usuario.

Atributos:
- identifier: Nombre interno y único del indexador (Clave Primaria).
- name: Nombre visual utilizado para la interfaz gráfica.
- auth_type: Tipo de autenticación requerida por el indexador (ej. 'cookie', 'login').
- cookie_string: (Cifrado) Cadena de texto de la cookie de sesión capturada del navegador.
- username: Nombre de usuario para sistemas de inicio de sesión automático.
- password: (Cifrado) Contraseña para sistemas de inicio de sesión automático.
- api_key: (Cifrado) Clave de acceso si el tracker expone una API oficial nativa.
- is_enabled: Interruptor global para activar o desactivar las búsquedas en este tracker.
- status: Último estado de conexión conocido ('ok', 'error', 'unknown').
"""
class IndexerConfig(SQLModel, table=True):
    identifier: str = Field(primary_key=True)
    name: str = Field(unique=True)
    auth_type: str
    cookie_string: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    api_key: Optional[str] = None
    is_enabled: bool = True
    status: str = "unknown"