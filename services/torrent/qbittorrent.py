import httpx
from core.app.logger import logger

# ------------------------------------------------------------
# Construye las cookies de autenticación para qBittorrent. Cuando
# el acceso está autorizado por whitelist de subred en v5, Kitsunarr
# puede operar sin SID y por tanto no debe enviar una cookie vacía.
# ------------------------------------------------------------
def _build_auth_cookies(sid_cookie: str | None) -> dict:
    return {"SID": sid_cookie} if sid_cookie else {}

# ------------------------------------------------------------
# Inicia sesión en qBittorrent desde Kitsunarr y valida respuestas
# de v4/v5. Devuelve SID cuando existe, cadena vacía si el acceso
# queda autorizado sin cookie (whitelist), o None si falla.
# ------------------------------------------------------------
async def qbittorrent_login(url: str, username: str, password: str) -> str | None:
    login_url = f"{url.rstrip('/')}/api/v2/auth/login"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(login_url, data={"username": username, "password": password})
            sid = resp.cookies.get("SID")
            if resp.status_code == 200 and "Ok." in resp.text:
                return sid or ""
            if resp.status_code == 204:
                return sid or ""

            if resp.status_code == 401:
                logger.error("❌ qBittorrent Login fallido: credenciales incorrectas.")
                return None

            if resp.status_code == 403:
                logger.error("❌ qBittorrent Login fallido: IP bloqueada o acceso prohibido.")
                return None

            if resp.status_code == 200 and resp.text.strip() in ["Fails.", "Ban."]:
                logger.error(f"❌ qBittorrent Login fallido: respuesta explícita '{resp.text.strip()}'.")
                return None

            logger.error(
                f"❌ qBittorrent Login fallido: respuesta inesperada "
                f"HTTP {resp.status_code} cuerpo={resp.text!r}"
            )
            return None
    except Exception as e:
        logger.error(f"❌ qBittorrent Error de conexión: {e}")
        return None

# ------------------------------------------------------------
# Obtiene la telemetría de un torrent concreto en qBittorrent usando
# su info hash, con cookie SID opcional, para mostrar estado,
# progreso, ratio y velocidades.
# ------------------------------------------------------------
async def get_torrent_telemetry(url: str, sid_cookie: str, info_hash: str) -> dict | None:
    api_url = f"{url.rstrip('/')}/api/v2/torrents/info?hashes={info_hash}"
    cookies = _build_auth_cookies(sid_cookie)
    try:
        async with httpx.AsyncClient(cookies=cookies, timeout=10.0) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()
            if data and len(data) > 0:
                t = data[0]
                return {
                    "progress": t.get("progress", 0.0),
                    "client_status": t.get("state", "unknown"),
                    "download_speed": t.get("dlspeed", 0),
                    "upload_speed": t.get("upspeed", 0),
                    "eta": t.get("eta", 0),
                    "peers_seeds": t.get("num_seeds", 0),
                    "peers_leechs": t.get("num_leechs", 0),
                    "ratio": t.get("ratio", 0.0),
                    "name": t.get("name"),
                    "size": t.get("size", 0),
                }
            return None
    except Exception as e:
        logger.error(f"❌ qBittorrent Error obteniendo telemetría para {info_hash}: {e}")
        return None

# ------------------------------------------------------------
# Devuelve la lista de torrents visibles en qBittorrent para que
# Kitsunarr pueda emparejarlos manualmente con fichas locales, con
# cookie SID opcional cuando la autorización viene por whitelist.
# ------------------------------------------------------------
async def get_all_unionfansub_torrents(url: str, sid_cookie: str) -> list:
    api_url = f"{url.rstrip('/')}/api/v2/torrents/info"
    cookies = _build_auth_cookies(sid_cookie)
    try:
        async with httpx.AsyncClient(cookies=cookies, timeout=15.0) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            all_torrents = resp.json()
            
            return [
                {
                    "info_hash": t.get("hash"),
                    "name": t.get("name"),
                    "size": t.get("size", 0),
                    "progress": t.get("progress", 0.0),
                    "state": t.get("state", "unknown"),
                    "tracker": t.get("tracker", ""),
                    "tags": t.get("tags", ""),
                    "category": t.get("category", ""),
                }
                for t in all_torrents
                if t.get("hash")
            ]
    except Exception as e:
        logger.error(f"❌ qBittorrent Error obteniendo lista general: {e}")
        return []
