# ==========================================
# SCRAPER DEL FORO (EXTRACCIÓN DE IMÁGENES)
# ==========================================
import httpx
import re
from bs4 import BeautifulSoup
from core.logger import logger

async def fetch_poster_url(forum_url: str, cookie_string: str) -> str | None:
    """
    Visita el tema (hilo) específico del foro de UnionFansub y extrae la URL de la portada original.
    Esto es necesario porque la página principal del tracker no incluye las imágenes en alta calidad.
    
    Flujo:
    1. Ajusta la URL si viene incompleta (//foro.unionfansub...).
    2. Descarga el HTML del post usando la sesión del usuario para saltar restricciones.
    3. Busca la etiqueta div con la clase 'portada' y extrae la URL de fondo (background-image).
    """
    if forum_url.startswith("//"):
        forum_url = f"https:{forum_url}"
        
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
    headers = {
        "User-Agent": user_agent, 
        "Cookie": cookie_string,
        "Accept": "text/html"
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=15.0) as client:
            response = await client.get(forum_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "lxml")
            
            portada_div = soup.find("div", class_="portada")
            if portada_div and portada_div.has_attr("style"):
                style_text = portada_div["style"]
                
                # Usamos Expresiones Regulares para sacar el link limpio de dentro de url('...')
                match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style_text)
                if match:
                    img_url = match.group(1)
                    if img_url.startswith("//"):
                        img_url = f"https:{img_url}"
                    return img_url
                    
    except Exception as e:
        logger.error(f"⚠️ Error intentando raspar el póster en el foro ({forum_url}): {e}")
        
    return None