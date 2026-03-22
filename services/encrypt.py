# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import os
import xml.etree.ElementTree as ET
from cryptography.fernet import Fernet
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# CONFIGURACIÓN DE SEGURIDAD
# ==========================================

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

SECRETS_DIR = os.getenv("KITSUNARR_SECRETS_DIR", "secrets")
SECRETS_FILE = os.path.join(SECRETS_DIR, "secrets.xml")

# ==========================================
# GESTIÓN DE LA LLAVE MAESTRA (SECRETS.XML)
# ==========================================

"""
Genera y recupera la llave de cifrado persistente desde un archivo XML.
Si el archivo no existe, crea uno nuevo con una llave aleatoria de 32 bytes.
Esta llave maestra no debe exponerse nunca al exterior.
"""
def get_or_create_master_key():
    os.makedirs(SECRETS_DIR, exist_ok=True)
    
    if not os.path.exists(SECRETS_FILE):
        key = Fernet.generate_key().decode()
        root = ET.Element("KitsunarrSettings")
        ET.SubElement(root, "EncryptionKey").text = key
        tree = ET.ElementTree(root)
        tree.write(SECRETS_FILE)
        return key
    
    tree = ET.parse(SECRETS_FILE)
    return tree.find("EncryptionKey").text

MASTER_KEY = get_or_create_master_key()
cipher_suite = Fernet(MASTER_KEY.encode())

# ==========================================
# FUNCIONES DE CIFRADO SIMÉTRICO (API KEYS / COOKIES)
# ==========================================

def encrypt_secret(plain_text: str) -> str:
    if not plain_text: 
        return ""
    return cipher_suite.encrypt(plain_text.encode()).decode()

def decrypt_secret(cipher_text: str) -> str:
    if not cipher_text: 
        return ""
    try:
        return cipher_suite.decrypt(cipher_text.encode()).decode()
    except Exception:
        return cipher_text

# ==========================================
# FUNCIONES DE HASHING (CONTRASEÑA ADMIN)
# ==========================================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False