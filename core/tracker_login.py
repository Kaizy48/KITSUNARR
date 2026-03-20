# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import httpx
from sqlmodel import Session, select

from core.database import engine
from core.logger import logger
from core.models.indexer import IndexerConfig


# ==========================================
# AUTENTICACIÓN Y GESTIÓN DE SESIONES
# ==========================================

"""
Realiza un inicio de sesión en dos pasos simulando exactamente el formulario 
de 'quick_login' desde la portada del tracker hacia el foro de MyBB.
Extrae y ensambla todas las cookies resultantes para mantener la sesión viva
de cara a las futuras descargas y búsquedas.
"""
async def attempt_unionfansub_login(username: str, password: str) -> str | None:
    tracker_url = "https://torrent.unionfansub.com/index.php"
    login_url = "https://foro.unionfansub.com/member.php"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0 (Kitsunarr; +https://github.com/Kaizy48/KITSUNARR)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Priority": "u=0, i",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-GPC": "1",
        "TE": "trailers",
        "Upgrade-Insecure-Requests": "1"
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=30.0) as client:
            
            logger.info(f"🔑 [Paso 1] Visitando el tracker para obtener cookies iniciales de sesión...")
            
            await client.get(tracker_url)
            
            logger.info("🔓 [Paso 1 Completado] Cookies iniciales obtenidas. Preparando POST...")

            payload = {
                "action": "do_login",
                "url": "//torrent.unionfansub.com/",
                "quick_login": "1",
                "quick_username": username,
                "quick_password": password,
                "submit": "Iniciar sesión",
                "quick_remember": "yes"
            }

            client.headers.update({
                "Origin": "https://torrent.unionfansub.com",
                "Referer": "https://torrent.unionfansub.com/",
                "Content-Type": "application/x-www-form-urlencoded"
            })

            logger.info("🔑 [Paso 2] Enviando credenciales mediante quick_login...")
            
            await client.post(login_url, data=payload)
            
            cookie_parts = []
            for name, value in client.cookies.items():
                cookie_parts.append(f"{name}={value}")
                
            full_cookie_string = "; ".join(cookie_parts)
            
            if "mybbuser" in full_cookie_string:
                logger.info("✅ Auto-Login exitoso. Cookie 'mybbuser' capturada y ensamblada.")
                return full_cookie_string
            else:
                logger.error(f"❌ Auto-Login fallido. El servidor no devolvió la cookie 'mybbuser'.")
                return None
                
    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"❌ Error de red durante el Auto-Login ({error_type}): {str(e)}")
        return None

"""
Función de rescate. Consulta la base de datos local en busca 
de credenciales guardadas bajo el método de 'Auto-Login'. Si las encuentra, 
intenta renovar la sesión caducada contra el tracker y actualiza la base de datos sin
interrumpir el flujo de las descargas o búsquedas en curso.
"""
async def auto_renew_cookie() -> str | None:
    with Session(engine) as session:
        indexer = session.exec(select(IndexerConfig).where(IndexerConfig.identifier == "unionfansub")).first()
        
        if indexer and indexer.auth_type == "login" and indexer.username and indexer.password:
            logger.warning("🔄 Cookie caducada detectada. Iniciando Auto-Renovación silenciosa...")
            
            new_cookie = await attempt_unionfansub_login(indexer.username, indexer.password)
            
            if new_cookie:
                indexer.cookie_string = new_cookie
                indexer.status = "ok"
                session.commit()
                logger.info("✅ Auto-Renovación completada. Resumiendo operaciones...")
                return new_cookie
            else:
                indexer.status = "error"
                session.commit()
                logger.error("❌ Auto-Renovación fallida. El tracker rechazó las credenciales.")
                
    return None