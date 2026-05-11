import os
import xml.etree.ElementTree as ET
from cryptography.fernet import Fernet
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

SECRETS_DIR = os.getenv("KITSUNARR_SECRETS_DIR", "secrets")
SECRETS_FILE = os.path.join(SECRETS_DIR, "secrets.xml")

# ------------------------------------------------------------
# Obtiene o crea la clave maestra persistente que Kitsunarr usa
# para cifrar secretos locales como cookies, API keys y credenciales.
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Cifra un secreto antes de guardarlo en la base de datos local de
# Kitsunarr.
# ------------------------------------------------------------
def encrypt_secret(plain_text: str) -> str:
    if not plain_text: 
        return ""
    return cipher_suite.encrypt(plain_text.encode()).decode()

# ------------------------------------------------------------
# Descifra un secreto guardado por Kitsunarr y mantiene compatibilidad
# con valores antiguos que todavía estuvieran en texto plano.
# ------------------------------------------------------------
def decrypt_secret(cipher_text: str) -> str:
    if not cipher_text: 
        return ""
    try:
        return cipher_suite.decrypt(cipher_text.encode()).decode()
    except Exception:
        return cipher_text

# ------------------------------------------------------------
# Genera el hash seguro de la contraseña del administrador de
# Kitsunarr.
# ------------------------------------------------------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

# ------------------------------------------------------------
# Verifica la contraseña introducida en el login contra el hash del
# administrador guardado por Kitsunarr.
# ------------------------------------------------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    return pwd_context.verify(plain_password, hashed_password)
