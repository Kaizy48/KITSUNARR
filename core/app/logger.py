import os
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = os.getenv("KITSUNARR_DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, "kitsunarr.log")
BAK_FILE = os.path.join(DATA_DIR, "kitsunarr.log.bak")

logger = logging.getLogger("kitsunarr")
logger.setLevel(logging.INFO)

if not logger.handlers:
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5120:
        try:
            if os.path.exists(BAK_FILE):
                os.remove(BAK_FILE)
            os.rename(LOG_FILE, BAK_FILE)
        except Exception:
            pass

    file_handler = RotatingFileHandler(
        LOG_FILE, 
        maxBytes=2*1024*1024,
        backupCount=1, 
        encoding='utf-8'
    )
    
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
