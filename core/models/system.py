# ==========================================
# MODELOS DE DATOS: CONFIGURACIÓN DEL SISTEMA E IA
# ==========================================
from typing import Optional
from sqlmodel import Field, SQLModel

class SystemConfig(SQLModel, table=True):
    """
    Guarda la configuración global del núcleo de Kitsunarr.
    Actualmente almacena la Clave API generada aleatoriamente que Sonarr 
    necesita para comunicarse de forma segura.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    api_key: str = Field(unique=True) # Clave de seguridad Torznab


class AIConfig(SQLModel, table=True):
    """
    Guarda las credenciales y preferencias del Motor de Inteligencia Artificial.
    Permite cambiar de proveedor (Gemini, OpenAI, Ollama) en caliente sin reiniciar.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # --- CONTROL DEL MOTOR ---
    is_enabled: bool = False      # Apagado general (Kill-switch) de cualquier función IA
    is_automated: bool = False    # Determina si el trabajador de fondo procesa los pendientes automáticamente
    
    # --- PROVEEDOR Y MODELO ---
    provider: str = "gemini"      # 'gemini', 'openai' u 'ollama'
    model_name: str = "gemini-1.5-flash" # Modelo específico a utilizar
    
    # --- CREDENCIALES Y CONEXIÓN ---
    api_key: str = ""             # Para proveedores en la nube (Google/OpenAI)
    base_url: str = "http://localhost:11434" # Para LLMs locales (Ollama)