# ==========================================
# CONFIGURACIÓN DE BASE DE DATOS Y PERSISTENCIA
# ==========================================
import os
from sqlmodel import SQLModel, create_engine, Session
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# GESTIÓN DE DIRECTORIOS
# ==========================================
# Leemos la ruta desde el .env. Esto es vital para Docker, ya que permite 
# al usuario mapear un volumen (ej. /app/data) y que la base de datos 
# no se borre al reiniciar el contenedor.
DATA_DIR = os.getenv("KITSUNARR_DATA_DIR", "data")

# Aseguramos que la carpeta exista antes de que SQLite intente crear el archivo.
os.makedirs(DATA_DIR, exist_ok=True)

# Definimos el archivo físico de la base de datos en la ruta dinámica.
sqlite_file_name = os.path.join(DATA_DIR, "kitsunarr.db")
sqlite_url = f"sqlite:///{sqlite_file_name}"


# ==========================================
# MOTOR DE SQLMODEL (SQLITE)
# ==========================================
# Creamos el Motor principal. 
# check_same_thread=False es necesario en FastAPI porque diferentes peticiones 
# web (asíncronas) pueden intentar acceder a SQLite al mismo tiempo.
engine = create_engine(
    sqlite_url, 
    echo=False, # Pon esto en True si alguna vez necesitas debugear consultas SQL en consola
    connect_args={"check_same_thread": False}
)


# ==========================================
# FUNCIONES DE GESTIÓN DE SESIÓN
# ==========================================

def create_db_and_tables():
    """
    Lee todas las clases que heredan de SQLModel (nuestros modelos en core/models/)
    y crea las tablas correspondientes en el archivo .db si no existen.
    Esta función es llamada por 'lifespan' en main.py al arrancar el servidor.
    """
    SQLModel.metadata.create_all(engine)

def get_session():
    """
    Generador de sesiones de base de datos.
    Se utiliza principalmente para inyección de dependencias (Dependency Injection) 
    en rutas futuras de FastAPI que requieran acceso a la DB.
    Asegura que la sesión se cierre correctamente tras cada petición.
    """
    with Session(engine) as session:
        yield session