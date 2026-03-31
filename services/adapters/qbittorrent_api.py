# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import httpx
from core.logger import logger

# ==========================================
# CLIENTE API QBITTORRENT (v2)
# ==========================================

"""
Realiza el inicio de sesión en la API de qBittorrent usando las credenciales
guardadas en el sistema y devuelve la cookie de sesión 'SID'.
"""
async def qbittorrent_login(url: str, username: str, password: str) -> str | None:
    login_url = f"{url.rstrip('/')}/api/v2/auth/login"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(login_url, data={"username": username, "password": password})
            
            if resp.status_code == 200 and "Ok." in resp.text:
                return resp.cookies.get("SID")
            else:
                logger.error(f"❌ qBittorrent Login fallido: Credenciales incorrectas o IP bloqueada.")
                return None
    except Exception as e:
        logger.error(f"❌ qBittorrent Error de conexión: {e}")
        return None

"""
Consulta el estado, la velocidad y el progreso de un único torrent
específico mediante su Info Hash. (Usado para la Telemetría en Vivo).
"""
async def get_torrent_telemetry(url: str, sid_cookie: str, info_hash: str) -> dict | None:
    api_url = f"{url.rstrip('/')}/api/v2/torrents/info"
    params = {"hashes": info_hash}
    cookies = {"SID": sid_cookie}
    
    try:
        async with httpx.AsyncClient(cookies=cookies, timeout=10.0) as client:
            resp = await client.get(api_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            if data and len(data) > 0:
                t = data[0]
                return {
                    "exists_in_client": True,
                    "client_status": t.get("state", "unknown"),
                    "progress": t.get("progress", 0.0),
                    "download_speed": t.get("dlspeed", 0),
                    "upload_speed": t.get("upspeed", 0),
                    "eta": t.get("eta", 8640000),
                    "peers_seeds": t.get("num_seeds", 0),
                    "peers_leechs": t.get("num_leechs", 0),
                    "ratio": t.get("ratio", 0.0)
                }
            return None
    except Exception as e:
        logger.error(f"❌ qBittorrent Error obteniendo telemetría para {info_hash}: {e}")
        return None

"""
Obtiene TODOS los torrents del cliente y filtra en memoria únicamente 
los que pertenecen a UnionFansub. (Usado para el Laboratorio de Torrents).
"""
async def get_all_unionfansub_torrents(url: str, sid_cookie: str) -> list:
    api_url = f"{url.rstrip('/')}/api/v2/torrents/info"
    cookies = {"SID": sid_cookie}
    
    try:
        async with httpx.AsyncClient(cookies=cookies, timeout=15.0) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            all_torrents = resp.json()
            
            union_torrents = [
                {
                    "info_hash": t.get("hash"),
                    "name": t.get("name"),
                    "size": t.get("size", 0),
                    "progress": t.get("progress", 0.0),
                    "state": t.get("state", "unknown")
                }
                for t in all_torrents 
                if "unionfansub.com" in (t.get("tracker", "")).lower()
            ]
            return union_torrents
    except Exception as e:
        logger.error(f"❌ qBittorrent Error listando torrents: {e}")
        return []