# ==========================================
# SISTEMA CENTRAL DE LOGS Y EVENTOS
# ==========================================
import os
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# RUTAS DE LOS ARCHIVOS DE REGISTRO
# ==========================================
# Al igual que la base de datos, los logs deben guardarse en el volumen persistente
DATA_DIR = os.getenv("KITSUNARR_DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, "kitsunarr.log")
BAK_FILE = os.path.join(DATA_DIR, "kitsunarr.log.bak")

# Creamos el objeto Logger principal al que llamarán todos los demás archivos
logger = logging.getLogger("kitsunarr")
logger.setLevel(logging.INFO)


# ==========================================
# CONFIGURACIÓN DE HANDLERS (CÓMO Y DÓNDE SE GUARDA)
# ==========================================

# Prevenimos que el logger agregue múltiples handlers si este archivo se importa varias veces
if not logger.handlers:
    
    # --- PREVENCIÓN DE BUG MULTIPROCESO (UVICORN) ---
    # Uvicorn, al arrancar con la opción 'reload=True', crea dos procesos de Python.
    # Esto a veces causa que ambos intenten escribir en el mismo archivo al mismo tiempo.
    # Para evitar que el archivo se corrompa, si el archivo existe y es grande, lo rotamos manualmente.
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5120:
        try:
            if os.path.exists(BAK_FILE):
                os.remove(BAK_FILE)
            os.rename(LOG_FILE, BAK_FILE)
        except Exception:
            pass

    # --- HANDLER 1: ARCHIVO DE TEXTO (ROTATIVO) ---
    # Guarda los logs en 'kitsunarr.log'.
    # Cuando el archivo supera los 2MB, lo renombra a .bak y crea uno nuevo, 
    # evitando que el servidor se quede sin espacio en disco por culpa de los logs.
    file_handler = RotatingFileHandler(
        LOG_FILE, 
        maxBytes=2*1024*1024, # 2 Megabytes
        backupCount=1, 
        encoding='utf-8'
    )
    
    # Definimos el formato visual: [Fecha/Hora] - [Nivel(INFO/ERROR)] - [Mensaje]
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # --- HANDLER 2: CONSOLA (TERMINAL) ---
    # Imprime exactamente los mismos mensajes en la terminal de Docker o PowerShell.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)