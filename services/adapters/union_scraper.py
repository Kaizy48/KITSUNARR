# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import asyncio
import re
import time
import urllib.parse
from datetime import datetime, timedelta
from email.utils import formatdate

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import HTTPException
from sqlmodel import Session, select

from core.database import engine
from core.logger import logger
from core.models.torrent import TorrentCache
from core.tracker_login import auto_renew_cookie
from services.adapters.forum_scraper import fetch_poster_url

load_dotenv()


# ==========================================
# FUNCIONES AUXILIARES DE PARSEO
# ==========================================

"""
Convierte fechas en formato de texto español al formato RFC estándar 
requerido por Sonarr en el XML Torznab.
"""
def parse_spanish_date_to_rfc(date_str: str) -> str:
    meses = {"Ene": "01", "Feb": "02", "Mar": "03", "Abr": "04", "May": "05", "Jun": "06", 
             "Jul": "07", "Ago": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dic": "12"}
    try:
        clean_str = date_str.replace(",", "")
        parts = clean_str.split()
        if len(parts) >= 4 and parts[1] in meses:
            day = parts[0].zfill(2)
            month = meses[parts[1]]
            year = parts[2]
            time_str = parts[3]
            dt = datetime.strptime(f"{year}-{month}-{day} {time_str}", "%Y-%m-%d %H:%M")
            return formatdate(dt.timestamp(), localtime=False, usegmt=True)
    except Exception:
        pass
    return formatdate(time.time(), localtime=False, usegmt=True)

"""
Analiza el título procesado por la IA y extrae los números de temporada para 
generar los atributos Torznab de Sonarr (soporta rangos como S01-S04 o temporadas únicas).
"""
def extract_seasons(title: str) -> list[int]:
    seasons = []
    range_match = re.search(r"S(\d{1,2})-S(\d{1,2})", title, re.IGNORECASE)
    if range_match:
        start = int(range_match.group(1))
        end = int(range_match.group(2))
        if start <= end and (end - start) < 20:
            seasons = list(range(start, end + 1))
    else:
        single_match = re.search(r"S(\d{1,2})", title, re.IGNORECASE)
        if single_match:
            seasons = [int(single_match.group(1))]
            
    return seasons

"""
Extrae la sinopsis, metadatos técnicos y caducidad del freeleech 
analizando el código HTML de la ficha de detalles de un torrent específico.
"""
def parse_ficha_metadata(html_content: str) -> dict:
    soup = BeautifulSoup(html_content, "lxml")
    ficha_table = soup.find("table", class_="ficha")
    
    result = {
        "extra_info": "", 
        "pub_date": formatdate(time.time(), localtime=False, usegmt=True),
        "freeleech_until": None,
        "description": "",
        "forum_url": None
    }
    
    freeleech_span = soup.find(string=re.compile(r"FREEleech durante", re.IGNORECASE))
    if freeleech_span:
        text = freeleech_span.strip()
        match = re.search(r"FREEleech durante\s*(?:(\d+)\s*dias?)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?", text, re.IGNORECASE)
        if match:
            days = int(match.group(1)) if match.group(1) else 0
            hours = int(match.group(2)) if match.group(2) else 0
            minutes = int(match.group(3)) if match.group(3) else 0
            total_seconds = (days * 86400) + (hours * 3600) + (minutes * 60)
            if total_seconds > 0:
                result["freeleech_until"] = datetime.utcnow() + timedelta(seconds=total_seconds)

    desc_td = soup.find("td", string=re.compile(r"Descripci[oó]n", re.IGNORECASE))
    if desc_td:
        desc_val_td = desc_td.find_next_sibling("td")
        if desc_val_td:
            result["description"] = desc_val_td.text.strip()

    if not ficha_table:
        return result

    metadata = []
    for row in ficha_table.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) == 2:
            key = cols[0].text.strip().lower()
            value = cols[1].get_text(separator=", ", strip=True)

            if "video" in key: 
                metadata.append(f"[{value}]") 
            elif "audio" in key: 
                metadata.append(f"[Audio: {value}]")
            elif "subtitulo" in key or "subtítulo" in key: 
                value = value.replace("Castellano", "Español (Castellano)")
                value = value.replace("Latino", "Español (Latino)")
                metadata.append(f"[Subs: {value}]")
            elif "descarga directa" in key: 
                link_tag = cols[1].find("a", href=re.compile(r"showthread\.php\?tid="))
                if link_tag:
                    result["forum_url"] = link_tag["href"]

    result["extra_info"] = " ".join(metadata)

    date_td = soup.find("td", string=re.compile(r"Añadido", re.IGNORECASE))
    if date_td:
        date_val_td = date_td.find_next_sibling("td")
        if date_val_td:
            result["pub_date"] = parse_spanish_date_to_rfc(date_val_td.text.strip())

    return result

"""
Convierte una cadena de texto que describe un tamaño (ej. '1.5 GB') en su 
equivalente numérico en bytes enteros, necesario para el protocolo Torznab.
"""
def parse_size_to_bytes(size_str: str) -> int:
    size_str = size_str.replace(",", ".").upper().strip()
    try:
        if "GB" in size_str: return int(float(size_str.replace("GB", "").strip()) * 1024**3)
        elif "MB" in size_str: return int(float(size_str.replace("MB", "").strip()) * 1024**2)
        elif "KB" in size_str: return int(float(size_str.replace("KB", "").strip()) * 1024)
        return 0
    except ValueError: return 0


# ==========================================
# SCRAPER PRINCIPAL Y GENERACIÓN XML
# ==========================================

"""
Ejecuta la búsqueda y extracción de datos del tracker principal simulando 
un navegador personalizado de Kitsunarr. Gestiona la caché, extrae metadatos adicionales de cada ficha, 
y devuelve los resultados en formato XML Torznab o una lista de IDs.
"""
async def search_unionfansub_html(query: str, cookie_string: str, base_url: str, api_key: str, offset: int = 0, interactivo: bool = False) -> str | list:
    search_param = urllib.parse.quote(query) if query else ""
    page_num = offset // 25 if offset > 0 else 0
    categories = "&c1=1&c2=1&c3=1&c9=1&c13=1&c15=1"
    
    url = f"https://torrent.unionfansub.com/browse.php?search={search_param}&incldead=0{categories}&page={page_num}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0 (Kitsunarr; +https://github.com/Kaizy48/KITSUNARR)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Host": "torrent.unionfansub.com",
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
        async with httpx.AsyncClient(follow_redirects=False, headers=headers, timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 302:
                logger.warning("⚠️ El tracker devolvió 302 (Redirección). Posible cookie caducada.")
                new_cookie = await auto_renew_cookie()
                
                if new_cookie:
                    headers["Cookie"] = new_cookie
                    response = await client.get(url, headers=headers)
                
            if response.status_code == 302:
                raise HTTPException(status_code=502, detail="Bloqueado por el tracker. La cookie expiró y no se pudo auto-renovar.")
                
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "lxml")
            torrents_table = soup.find("table", class_="tlist")
            
            torrents_data = [] 
            interactive_ids = [] 
            
            if not torrents_table: 
                return [] if interactivo else generate_torznab_xml([], query)

            rows = torrents_table.find_all("tr")[1:] 
            
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 11: continue
                
                title_tag = cols[1].find("b", class_="name")
                title = title_tag.text.strip() if title_tag else "Sin título"
                
                fansub_tag = cols[1].find("span", re.compile(r"fansub.*"))
                fansub_clean = fansub_tag.text.replace("[", "").replace("]", "").strip() if fansub_tag else ""
                
                original_title = f"[UnionFansub | {fansub_clean}] {title}" if fansub_clean else f"[UnionFansub] {title}"

                details_link = cols[1].find("a", href=True)
                torrent_id = re.search(r"id=(\d+)", details_link['href']).group(1) if details_link else ""
                
                if not torrent_id: continue

                if torrent_id not in interactive_ids:
                    interactive_ids.append(torrent_id)

                size_bytes = parse_size_to_bytes(cols[7].text.strip())
                seeders = cols[9].text.strip()
                leechers = cols[10].text.strip()

                db_torrent_data = None
                with Session(engine) as db_session:
                    db_torrent = db_session.exec(select(TorrentCache).where(TorrentCache.guid == torrent_id)).first()
                    if db_torrent:
                        db_torrent_data = {
                            "enriched_title": db_torrent.enriched_title,
                            "pub_date": db_torrent.pub_date,
                            "freeleech_until": db_torrent.freeleech_until,
                            "ai_translated_title": db_torrent.ai_translated_title,
                            "ai_status": db_torrent.ai_status,
                            "tvdb_id": db_torrent.tvdb_id
                        }

                if db_torrent_data:
                    logger.info(f"⚡ [CACHÉ] Encontrado en DB: {torrent_id}")
                    pub_date = db_torrent_data["pub_date"] or formatdate(time.time(), localtime=False, usegmt=True)
                    
                    if not interactivo:
                        torrents_data.append({
                            "title": db_torrent_data["enriched_title"], "guid": f"{torrent_id}_base",
                            "link": f"{base_url}/api/download/{torrent_id}_base?apikey={api_key}", "size_bytes": size_bytes,
                            "seeders": seeders, "leechers": leechers, "pub_date": pub_date, 
                            "freeleech_until": db_torrent_data["freeleech_until"],
                            "tvdb_id": None, "seasons": []
                        })
                        
                        if db_torrent_data["ai_translated_title"] and db_torrent_data["ai_status"] in ["Listo", "Manual"]:
                            torrents_data.append({
                                "title": db_torrent_data["ai_translated_title"], "guid": f"{torrent_id}_ai",
                                "link": f"{base_url}/api/download/{torrent_id}_ai?apikey={api_key}", "size_bytes": size_bytes,
                                "seeders": seeders, "leechers": leechers, "pub_date": pub_date, 
                                "freeleech_until": db_torrent_data["freeleech_until"],
                                "tvdb_id": db_torrent_data["tvdb_id"],
                                "seasons": extract_seasons(db_torrent_data["ai_translated_title"])
                            })
                
                else:
                    logger.info(f"🔍 [NUEVO] Raspando Ficha de: {torrent_id}")
                    details_url = f"https://torrent.unionfansub.com/details.php?id={torrent_id}&hit=1"
                    
                    try:
                        details_resp = await client.get(details_url)
                        details_resp.raise_for_status()
                        
                        ficha_data = parse_ficha_metadata(details_resp.text)
                        enriched_title = f"{original_title} {ficha_data['extra_info']}".strip()
                        
                        poster_url = None
                        if ficha_data.get("forum_url"):
                            poster_url = await fetch_poster_url(ficha_data["forum_url"], cookie_string)
                        
                        with Session(engine) as db_session:
                            try:
                                existing = db_session.exec(select(TorrentCache).where(TorrentCache.guid == torrent_id)).first()
                                if not existing:
                                    new_torrent = TorrentCache(
                                        indexer="unionfansub", guid=torrent_id, fansub_name=fansub_clean,
                                        original_title=original_title, enriched_title=enriched_title,
                                        description=ficha_data['description'], ai_status="Pendiente",
                                        poster_url=poster_url, size_bytes=size_bytes, 
                                        download_url=f"{base_url}/api/download/{torrent_id}_base",
                                        pub_date=ficha_data['pub_date'], freeleech_until=ficha_data['freeleech_until']
                                    )
                                    db_session.add(new_torrent)
                                    db_session.commit()
                            except Exception as db_e:
                                db_session.rollback()
                                logger.error(f"⚠️ Error base de datos guardando ficha {torrent_id}: {db_e}")

                        if not interactivo:
                            torrents_data.append({
                                "title": enriched_title, "guid": f"{torrent_id}_base",
                                "link": f"{base_url}/api/download/{torrent_id}_base?apikey={api_key}", "size_bytes": size_bytes,
                                "seeders": seeders, "leechers": leechers, "pub_date": ficha_data['pub_date'],
                                "freeleech_until": ficha_data['freeleech_until'],
                                "tvdb_id": None, "seasons": []
                            })
                        
                        await asyncio.sleep(1.5)
                        
                    except Exception as e:
                        logger.error(f"⚠️ Error de red/raspado en ficha {torrent_id}: {e}")
                            
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Error conectando al tracker: {e}")
    
    if interactivo:
        return interactive_ids

    return generate_torznab_xml(torrents_data, query)

"""
Construye la estructura XML válida bajo el esquema de Torznab para la respuesta.
Inyecta dinámicamente los atributos Torznab avanzados de TVDB y Temporada para Sonarr.
"""
def generate_torznab_xml(torrents: list, query: str) -> str:
    xml_items = ""
    for t in torrents:
        size_bytes = t['size_bytes'] if t['size_bytes'] > 0 else 1
        pub_date = t.get('pub_date') or formatdate(time.time(), localtime=False, usegmt=True)
        
        dl_factor = 1 
        if t.get('freeleech_until') and datetime.utcnow() < t['freeleech_until']:
            dl_factor = 0 
            
        extra_attrs = ""
        if t.get('tvdb_id') and str(t['tvdb_id']).lower() not in ["null", "none", ""]:
            extra_attrs += f'\n            <torznab:attr name="tvdbid" value="{t["tvdb_id"]}" />'
            
        if t.get('seasons'):
            for s in t['seasons']:
                extra_attrs += f'\n            <torznab:attr name="season" value="{s}" />'
        
        xml_items += f"""
        <item>
            <title><![CDATA[{t['title']}]]></title>
            <guid isPermaLink="false">{t['guid']}</guid>
            <link><![CDATA[{t['link']}]]></link>
            <pubDate>{pub_date}</pubDate>
            <description><![CDATA[Torrent de UnionFansub]]></description>
            <enclosure url="{t['link']}" length="{size_bytes}" type="application/x-bittorrent" />
            <torznab:attr name="category" value="5070" />
            <torznab:attr name="seeders" value="{t['seeders']}" />
            <torznab:attr name="peers" value="{int(t['seeders']) + int(t['leechers'])}" />
            <torznab:attr name="size" value="{size_bytes}" />
            <torznab:attr name="downloadvolumefactor" value="{dl_factor}" />
            <torznab:attr name="uploadvolumefactor" value="1" />{extra_attrs}
        </item>"""
        
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
        <channel>
            <title>Kitsunarr Proxy - UnionFansub</title>
            <description>Resultados para '{query}'</description>
            <language>es-es</language>
            {xml_items}
        </channel>
    </rss>
    """


# ==========================================
# TEST DE CONEXIÓN Y ESTADO
# ==========================================

"""
Realiza una petición rápida de prueba al rastreador web utilizando las cabeceras 
que simulan un navegador personalizado de Kitsunarr para verificar si la cookie actual sigue siendo válida.
"""
async def test_unionfansub_connection(cookie_string: str) -> bool:
    url = "https://torrent.unionfansub.com/browse.php"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0 (Kitsunarr/0.2.1; +https://github.com/Kaizy48/KITSUNARR)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Host": "torrent.unionfansub.com",
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
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.get(url, headers=headers, timeout=10.0)
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error en Ping al Tracker: {e}")
        return False