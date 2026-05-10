import os
from dotenv import load_dotenv
from sqlmodel import SQLModel, create_engine, Session

from core.database import models

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
        "timeout": 20.0
    }
)

# ------------------------------------------------------------
# Inicializa la base de datos SQLite de Kitsunarr y crea las tablas
# necesarias para configuración, caché, TVDB e indexadores.
# ------------------------------------------------------------
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    with engine.connect() as con:
        con.exec_driver_sql("PRAGMA journal_mode=WAL;")

# ------------------------------------------------------------
# Proporciona sesiones de base de datos a endpoints y servicios de
# Kitsunarr.
# ------------------------------------------------------------
def get_session():
    with Session(engine) as session:
        yield session
