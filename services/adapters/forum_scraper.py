# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import re

import httpx
from bs4 import BeautifulSoup

from core.logger import logger


# ==========================================
# SCRAPER DEL FORO (EXTRACCIÓN DE IMÁGENES)
# ==========================================

"""
Visita el tema específico (hilo) del foro asociado a un release y extrae 
la URL de la portada original de alta calidad contenida en el diseño de la página.
Simula un navegador completo identificandose como Kitsunarr.
"""
async def fetch_poster_url(forum_url: str, cookie_string: str) -> str | None:
    if forum_url.startswith("//"):
        forum_url = f"https:{forum_url}"
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0 (Kitsunarr; +https://github.com/Kaizy48/KITSUNARR)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Host": "foro.unionfansub.com",
        "Priority": "u=0, i",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-GPC": "1",
        "TE": "trailers",
        "Upgrade-Insecure-Requests": "1",
        "Cookie": cookie_string
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=15.0) as client:
            response = await client.get(forum_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "lxml")
            
            portada_div = soup.find("div", class_="portada")
            if portada_div and portada_div.has_attr("style"):
                style_text = portada_div["style"]
                
                match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style_text)
                if match:
                    img_url = match.group(1)
                    if img_url.startswith("//"):
                        return f"https:{img_url}"
                    elif img_url.startswith("/"):
                        return f"https://foro.unionfansub.com{img_url}"
                    return img_url
                    
    except httpx.HTTPStatusError as e:
        logger.error(f"⚠️ Error intentando raspar el póster en el foro ({forum_url}): HTTP {e.response.status_code}")
    except Exception as e:
        logger.error(f"⚠️ Error de conexión raspando póster ({forum_url}): {e}")
        
    return None