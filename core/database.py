# ==========================================
# CONFIGURACIÓN DE BASE DE DATOS Y PERSISTENCIA
# ==========================================
import os
from sqlmodel import SQLModel, create_engine, Session
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.getenv("KITSUNARR_DATA_DIR", "data")

os.makedirs(DATA_DIR, exist_ok=True)

sqlite_file_name = os.path.join(DATA_DIR, "kitsunarr.db")
sqlite_url = f"sqlite:///{sqlite_file_name}"

engine = create_engine(
    sqlite_url, 
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": 20.0  # <--- Este es el Mutex
    }
)


# ==========================================
# FUNCIONES DE GESTIÓN DE SESIÓN
# ==========================================

"""
Lee todas las clases que heredan de SQLModel y crea las tablas correspondientes 
en el archivo de base de datos SQLite si no existen.
Esta función es llamada internamente al arrancar el servidor FastAPI en el ciclo 'lifespan'.
"""
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    with engine.connect() as con:
        con.exec_driver_sql("PRAGMA journal_mode=WAL;")

"""
Generador de sesiones de base de datos.
Proporciona una sesión activa para interactuar con la base de datos y se asegura 
de cerrarla automáticamente cuando la operación termina. Se utiliza principalmente 
para la inyección de dependencias en las rutas web de FastAPI.

Retorna:
    Session: Objeto de sesión de SQLModel conectado al motor de base de datos local.
"""
def get_session():
    with Session(engine) as session:
        yield session