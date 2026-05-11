import re
import time
import json
import urllib.parse
import asyncio
from datetime import datetime, timedelta
from email.utils import formatdate
from typing import List, Dict, Any, Optional

import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import TorrentCache
from core.app.logger import logger
from core.app.indexers.base import BaseIndexer

# ------------------------------------------------------------
# Indexador nativo de Kitsunarr para Union Fansub. Gestiona login,
# búsqueda paginada, scraping profundo de fichas, portadas, metadatos
# técnicos y descarga de archivos .torrent.
# ------------------------------------------------------------
class UnionFansubIndexer(BaseIndexer):
    GUID_PREFIX = "UNF"
    
    # ------------------------------------------------------------
    # Identificador interno del indexador Union Fansub dentro de
    # Kitsunarr.
    # ------------------------------------------------------------
    @property
    def identifier(self) -> str:
        return "unionfansub"

    # ------------------------------------------------------------
    # Nombre visible del indexador Union Fansub en la interfaz y logs.
    # ------------------------------------------------------------
    @property
    def name(self) -> str:
        return "Union Fansub"

    # ------------------------------------------------------------
    # Genera las cabeceras HTTP base que Kitsunarr usa para comunicarse
    # con las páginas de Union Fansub.
    # ------------------------------------------------------------
    def _get_base_headers(self) -> dict:
        return {
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

    # ------------------------------------------------------------
    # Convierte las fechas en español del tracker a formato RFC para
    # que Sonarr y la caché de Kitsunarr las consuman correctamente.
    # ------------------------------------------------------------
    def _parse_spanish_date_to_rfc(self, date_str: str) -> str:
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

    # ------------------------------------------------------------
    # Normaliza el nombre del fansub encontrado en Union Fansub al
    # formato de etiqueta usado por Kitsunarr.
    # ------------------------------------------------------------
    def _format_fansub_label(self, raw_text: str) -> str:
        clean = (raw_text or "").strip().strip("[]")
        clean = re.sub(r"\s+", " ", clean)
        if not clean:
            return "[Union Fansub]"
        if re.search(r"union\s*fansub", clean, re.IGNORECASE):
            return f"[{clean}]" if not clean.startswith("[") else clean
        return f"[Union Fansub | {clean}]"

    # ------------------------------------------------------------
    # Construye el título original de Kitsunarr combinando etiqueta de
    # fansub y nombre del torrent del tracker.
    # ------------------------------------------------------------
    def _build_original_title(self, fansub_label: str, title: str) -> str:
        label = (fansub_label or "").strip()
        if not label:
            return title or "Sin título"
        if label.startswith("[") and label.endswith("]"):
            return f"{label} {title}".strip()
        return f"[{label}] {title}".strip()

    # ------------------------------------------------------------
    # Extrae el fansub desde una fila de resultados del tracker para
    # generar títulos consistentes antes del raspado profundo.
    # ------------------------------------------------------------
    def _extract_fansub_from_list_row(self, cell, title: str) -> str:
        if cell:
            tag = cell.find("span", class_=re.compile(r"fansub", re.IGNORECASE))
            if tag:
                text = tag.get_text(" ", strip=True)
                if text:
                    return self._format_fansub_label(text)

            cell_text = cell.get_text(" ", strip=True)
            match = re.search(r"\[([^\]]*?(fansub|subs?)[^\]]*)\]", cell_text, re.IGNORECASE)
            if match:
                return self._format_fansub_label(match.group(1))

        if title:
            match = re.search(r"\[([^\]]*?(fansub|subs?)[^\]]*)\]", title, re.IGNORECASE)
            if match:
                return self._format_fansub_label(match.group(1))

        return "Union Fansub"

    # ------------------------------------------------------------
    # Extrae el fansub desde la ficha detallada del torrent cuando la
    # lista de resultados no aporta suficiente información.
    # ------------------------------------------------------------
    def _extract_fansub_from_details(self, html_content: str) -> Optional[str]:
        soup = BeautifulSoup(html_content, "lxml")

        h2_fansub = soup.select_one("h2.fansub")
        if h2_fansub:
            text = h2_fansub.get_text(" ", strip=True)
            if text:
                return self._format_fansub_label(text)

        tag = soup.find(["span", "div", "b"], class_=re.compile(r"fansub", re.IGNORECASE))
        if tag:
            text = tag.get_text(" ", strip=True)
            if text:
                return self._format_fansub_label(text)

        label_td = soup.find("td", string=re.compile(r"fansub|fansubber|subt[ií]tulos|subs", re.IGNORECASE))
        if label_td:
            value_td = label_td.find_next_sibling("td")
            if value_td:
                text = value_td.get_text(" ", strip=True)
                if text:
                    return self._format_fansub_label(text)

        title_candidates = []
        for selector in ["h1", "h2", ".titulo", ".name", "td#title", "div#title"]:
            el = soup.select_one(selector)
            if el:
                title_candidates.append(el.get_text(" ", strip=True))

        if soup.title and soup.title.string:
            title_candidates.append(soup.title.string)

        for text in title_candidates:
            match = re.search(r"\[([^\]]+)\]", text or "")
            if match:
                return self._format_fansub_label(match.group(1))

        return None

    # ------------------------------------------------------------
    # Raspa la ficha detallada de Union Fansub para obtener sinopsis,
    # freeleech, archivos, fecha, enlace de foro y tags técnicos.
    # ------------------------------------------------------------
    def _parse_ficha_metadata(self, html_content: str) -> dict:
        soup = BeautifulSoup(html_content, "lxml")
        ficha_table = soup.find("table", class_="ficha")
        
        result = {
            "extra_info": "", 
            "pub_date": formatdate(time.time(), localtime=False, usegmt=True),
            "freeleech_until": None,
            "description": "",
            "forum_url": None,
            "raw_filenames": None,
            "tags": None
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

        file_list = []
        cfilelist = soup.find("td", id="cfilelist")
        if cfilelist:
            for row in cfilelist.find_all("tr"):
                cols = row.find_all("td")
                if len(cols) == 2 and "colhead" not in cols[0].get("class", []):
                    file_list.append(cols[0].text.strip())
        if file_list:
            result["raw_filenames"] = json.dumps(file_list, ensure_ascii=False)

        if not ficha_table:
            return result

        metadata = []
        tags_list = []
        
        for row in ficha_table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) == 2:
                key = cols[0].text.strip().lower()
                value = cols[1].get_text(separator=", ", strip=True)
                v_lower = value.lower()

                if "video" in key: 
                    metadata.append(f"[{value}]")
                    if "4k" in v_lower or "2160" in v_lower: tags_list.append("4K")
                    elif "1440" in v_lower or "2k" in v_lower: tags_list.append("1440p")
                    elif "1080" in v_lower: tags_list.append("1080p")
                    elif "720" in v_lower: tags_list.append("720p")
                    elif "480" in v_lower: tags_list.append("480p")
                    
                    if "hevc" in v_lower or "x265" in v_lower or "h.265" in v_lower: tags_list.append("HEVC")
                    elif "h.264" in v_lower or "x264" in v_lower or "avc" in v_lower: tags_list.append("H.264")
                    elif "divx" in v_lower: tags_list.append("DivX")
                    elif "mpeg2" in v_lower: tags_list.append("MPEG2")
                    elif "wmv" in v_lower: tags_list.append("WMV")
                    
                    if "blu-ray4k" in v_lower or "bd4k" in v_lower: tags_list.append("Blu-ray4K")
                    elif "blu-ray" in v_lower or "bd" in v_lower: tags_list.append("Blu-ray")
                    elif "hd-dvd" in v_lower: tags_list.append("HD-DVD")
                    elif "web" in v_lower: tags_list.append("Web")
                    elif "hdtv" in v_lower: tags_list.append("HDTV")
                    elif "dvd" in v_lower: tags_list.append("DVD")
                    elif "dvb" in v_lower: tags_list.append("DVB")
                    elif "laserdisc" in v_lower: tags_list.append("Laserdisc")
                    elif "vhs" in v_lower: tags_list.append("VHS")
                    elif "tv" in v_lower.split() or "tv," in v_lower: tags_list.append("TV")
                    
                elif "audio" in key: 
                    metadata.append(f"[Audio: {value}]")
                    a_parts = [p.strip() for p in value.split(",")]
                    
                    current_audio_tags = []
                    audio_codecs = ["aac", "ac3", "flac", "mp3", "ogg", "alac", "wav", "dts"]
                    
                    for p in a_parts:
                        if not p: continue
                        clean_p = p.replace("(", "").replace(")", "").strip()
                        if clean_p.lower() in audio_codecs:
                            if current_audio_tags:
                                current_audio_tags[-1] = f"{current_audio_tags[-1]} {clean_p.upper()}"
                            else:
                                current_audio_tags.append(clean_p.upper())
                        else:
                            current_audio_tags.append(clean_p)
                            
                    for tag in current_audio_tags:
                        tags_list.append(f"Audio: {tag}")

                elif "subtitulo" in key or "subtítulo" in key: 
                    value = value.replace("Castellano", "Español (Castellano)").replace("Latino", "Español (Latino)")
                    metadata.append(f"[Subs: {value}]")
                    s_parts = [p.strip() for p in value.split(",")]
                    for p in s_parts:
                        if p: tags_list.append(f"Subs: {p}")
                        
                elif "contenedor" in key:
                    metadata.append(f"[{value}]")
                    if "mkv" in v_lower: tags_list.append("MKV")
                    elif "mp4" in v_lower: tags_list.append("MP4")
                    elif "avi" in v_lower: tags_list.append("AVI")
                    elif "flv" in v_lower: tags_list.append("FLV")
                    elif "ogm" in v_lower: tags_list.append("OGM")
                    
                    if "softsubs" in v_lower: tags_list.append("Softsubs")
                    if "hardsubs" in v_lower: tags_list.append("Hardsubs")
                    if "remux" in v_lower: tags_list.append("Remux")
                    
                elif "característica" in key or "caracteristica" in key:
                    if "ordered chapters" in v_lower: tags_list.append("Ordered Chapters")
                    if "softsubs" in v_lower: tags_list.append("Softsubs")
                    if "hardsubs" in v_lower: tags_list.append("Hardsubs")

                elif "descarga directa" in key:
                    link_tag = cols[1].find("a", href=re.compile(r"showthread\.php\?tid="))
                    if link_tag:
                        result["forum_url"] = link_tag["href"]

        if not result["forum_url"]:
            any_thread_link = soup.find("a", href=re.compile(r"showthread\.php\?tid="))
            if any_thread_link:
                result["forum_url"] = any_thread_link.get("href")

        result["extra_info"] = " ".join(metadata)
        
        if tags_list:
            unique_tags = list(dict.fromkeys(tags_list))
            result["tags"] = json.dumps(unique_tags, ensure_ascii=False)

        date_td = soup.find("td", string=re.compile(r"Añadido", re.IGNORECASE))
        if date_td:
            date_val_td = date_td.find_next_sibling("td")
            if date_val_td:
                result["pub_date"] = self._parse_spanish_date_to_rfc(date_val_td.text.strip())

        return result
    
    # ------------------------------------------------------------
    # Convierte el tamaño textual del tracker a bytes para que
    # Kitsunarr pueda informar el peso real por Torznab y en la UI.
    # ------------------------------------------------------------
    def _parse_size_to_bytes(self, size_str: str) -> int:
        size_str = size_str.replace(",", ".").upper().strip()
        try:
            if "GB" in size_str: return int(float(size_str.replace("GB", "").strip()) * 1024**3)
            elif "MB" in size_str: return int(float(size_str.replace("MB", "").strip()) * 1024**2)
            elif "KB" in size_str: return int(float(size_str.replace("KB", "").strip()) * 1024)
            return 0
        except ValueError: return 0

    # ------------------------------------------------------------
    # Convierte contadores del tracker a enteros, incluyendo formatos
    # con separadores de miles usados por Union Fansub.
    # ------------------------------------------------------------
    def _parse_tracker_int(self, value: str) -> int:
        clean = re.sub(r"[^\d]", "", value or "")
        return int(clean) if clean else 0

    # ------------------------------------------------------------
    # Inicia sesión en Union Fansub desde Kitsunarr y devuelve la
    # cookie necesaria para búsquedas, fichas, portadas y descargas.
    # ------------------------------------------------------------
    async def login(self, username: str, password: str) -> Optional[str]:
        tracker_url = "https://torrent.unionfansub.com/index.php"
        login_url = "https://foro.unionfansub.com/member.php"
        headers = self._get_base_headers()

        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=30.0) as client:
                logger.info(f"🔑 [INDEXER] [{self.name}] Visitando el tracker para obtener cookies iniciales...")
                await client.get(tracker_url)
                
                payload = {
                    "action": "do_login", "url": "//torrent.unionfansub.com/",
                    "quick_login": "1", "quick_username": username,
                    "quick_password": password, "submit": "Iniciar sesión", "quick_remember": "yes"
                }
                
                client.headers.update({
                    "Origin": "https://torrent.unionfansub.com",
                    "Referer": "https://torrent.unionfansub.com/",
                    "Content-Type": "application/x-www-form-urlencoded"
                })
                
                await client.post(login_url, data=payload)
                
                full_cookie_string = "; ".join([f"{name}={value}" for name, value in client.cookies.items()])
                
                if "mybbuser" in full_cookie_string:
                    logger.info(f"✅ [INDEXER] [{self.name}] Login exitoso. Cookie 'mybbuser' generada.")
                    return full_cookie_string
                return None
        except Exception as e:
            logger.error(f"❌ [INDEXER] [{self.name}] Error de red durante el login: {e}")
            return None

    # ------------------------------------------------------------
    # Comprueba si la cookie configurada permite acceder al explorador
    # de torrents de Union Fansub.
    # ------------------------------------------------------------
    async def test_connection(self, cookie_string: str) -> bool:
        url = "https://torrent.unionfansub.com/browse.php"
        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "torrent.unionfansub.com"
        
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code == 200
        except:
            return False

    # ------------------------------------------------------------
    # Extrae de una página de búsqueda de Union Fansub las filas de
    # torrents con ID, fansub, título, tamaño, semillas y pares.
    # ------------------------------------------------------------
    def _parse_search_rows(self, html_content: str) -> list[dict]:
        soup = BeautifulSoup(html_content, "lxml")
        torrents_table = soup.find("table", class_="tlist")
        if not torrents_table:
            return []

        parsed_rows = []
        for row in torrents_table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 11:
                continue

            title_tag = cols[1].find("b", class_="name")
            title = title_tag.text.strip() if title_tag else "Sin tÃ­tulo"

            formatted_fansub = self._extract_fansub_from_list_row(cols[1], title)
            original_title = self._build_original_title(formatted_fansub, title)

            details_link = cols[1].find("a", href=True)
            torrent_id_match = re.search(r"id=(\d+)", details_link["href"]) if details_link else None
            torrent_id = torrent_id_match.group(1) if torrent_id_match else ""
            if not torrent_id:
                continue

            seeders = self._parse_tracker_int(cols[9].get_text(" ", strip=True))
            leechers = self._parse_tracker_int(cols[10].get_text(" ", strip=True))

            parsed_rows.append({
                "guid": f"{self.GUID_PREFIX}-{torrent_id}",
                "source_guid": torrent_id,
                "fansub": formatted_fansub,
                "raw_title": title,
                "original_title": original_title,
                "size_bytes": self._parse_size_to_bytes(cols[7].text.strip()),
                "seeders": seeders,
                "leechers": leechers,
            })

        return parsed_rows

    # ------------------------------------------------------------
    # Detecta si la búsqueda actual de Union Fansub tiene una página
    # posterior para continuar el raspado paginado.
    # ------------------------------------------------------------
    def _has_next_search_page(self, html_content: str, current_page: int) -> bool:
        soup = BeautifulSoup(html_content, "lxml")
        for link in soup.find_all("a", href=True):
            href = link.get("href") or ""
            parsed = urllib.parse.urlparse(href)
            params = urllib.parse.parse_qs(parsed.query)
            for raw_page in params.get("page", []):
                try:
                    if int(raw_page) > current_page:
                        return True
                except Exception:
                    continue
        return False

    # ------------------------------------------------------------
    # Función de scraping al indexador Union Fansub para realizar una
    # consulta. Una vez recibimos los resultados comprobamos con el ID
    # de la ficha si lo tenemos en caché y, en caso negativo, hacemos
    # scraping profundo de la ficha. Si existen páginas siguientes y
    # la búsqueda tiene texto, repetimos en la siguiente página.
    # ------------------------------------------------------------
    async def search(self, query: str, cookie_string: str) -> List[Dict[str, Any]]:
        encoded_query = urllib.parse.quote_plus(query) if query else ""
        categories = "&c13=1&c1=1&c2=1&c3=1"
        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "torrent.unionfansub.com"
        
        results = []
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
                parsed_rows = []
                seen_page_guids = set()

                has_search_query = bool(query and query.strip())
                page = 0

                while True:
                    url = f"https://torrent.unionfansub.com/browse.php?search={encoded_query}&incldead=0{categories}&page={page}"
                    logger.info(f"🔎 [INDEXER] [{self.name}] URL búsqueda tracker: {url}")
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 302:
                        raise Exception("El tracker rechazó la cookie (Redirección 302).")
                    resp.raise_for_status()

                    if "No se ha encontrado nada" in resp.text:
                        if page == 0:
                            return []
                        break

                    page_rows = self._parse_search_rows(resp.text)
                    new_rows = 0
                    for item in page_rows:
                        guid = item.get("guid")
                        if guid and guid not in seen_page_guids:
                            seen_page_guids.add(guid)
                            parsed_rows.append(item)
                            new_rows += 1

                    has_next = self._has_next_search_page(resp.text, page)
                    logger.info(
                        f"📄 [INDEXER] [{self.name}] Página {page}: filas={len(page_rows)}, "
                        f"nuevas={new_rows}, siguiente={'sí' if has_next else 'no'}."
                    )
                    if not has_next:
                        break
                    if not has_search_query:
                        break
                    page += 1
                    await asyncio.sleep(0.4)

                if not parsed_rows:
                    return []

                torrent_ids = [item["guid"] for item in parsed_rows]
                cached_by_guid = {}
                with Session(engine) as db_session:
                    cached_rows = db_session.exec(
                        select(TorrentCache).where(TorrentCache.guid.in_(torrent_ids))
                    ).all()
                    cached_by_guid = {row.guid: row for row in cached_rows}

                cache_hits = 0
                cache_without_poster = 0
                deep_scraped = 0
                deep_scrape_errors = 0

                for item in parsed_rows:
                    guid = item["guid"]
                    source_guid = item["source_guid"]
                    incoming_fansub = item["fansub"]
                    base_title = item["raw_title"]
                    original_title = item["original_title"]
                    size_bytes = item["size_bytes"]
                    seeders = item["seeders"]
                    leechers = item["leechers"]

                    db_torrent = cached_by_guid.get(guid)

                    if db_torrent:
                        cache_hits += 1
                        if not db_torrent.poster_url:
                            cache_without_poster += 1
                        results.append({
                            "guid": db_torrent.guid,
                            "source_guid": db_torrent.source_guid or source_guid,
                            "original_title": original_title,
                            "title": db_torrent.enriched_title or original_title,
                            "description": db_torrent.description,
                            "poster_url": db_torrent.poster_url,
                            "size_bytes": size_bytes,
                            "seeders": seeders,
                            "leechers": leechers,
                            "publish_date": db_torrent.pub_date,
                            "freeleech_until": db_torrent.freeleech_until,
                            "raw_filenames": db_torrent.raw_filenames,
                            "tags": db_torrent.tags,
                            "fansub": incoming_fansub or db_torrent.fansub_name
                        })
                    else:
                        deep_scraped += 1
                        details_url = f"https://torrent.unionfansub.com/details.php?id={source_guid}&hit=1"
                        
                        try:
                            details_resp = await client.get(details_url, headers=headers)
                            ficha_data = self._parse_ficha_metadata(details_resp.text)
                            detail_fansub = self._extract_fansub_from_details(details_resp.text)
                            if detail_fansub:
                                formatted_fansub = detail_fansub
                                original_title = self._build_original_title(formatted_fansub, base_title)
                            
                            if not ficha_data.get("raw_filenames"):
                                try:
                                    fl_url = f"https://torrent.unionfansub.com/filelist.php?id={source_guid}&ajax=1"
                                    fl_resp = await client.get(fl_url, headers=headers)
                                    fl_soup = BeautifulSoup(fl_resp.text, "lxml")
                                    
                                    file_list = []
                                    for tr_row in fl_soup.find_all("tr"):
                                        tr_cols = tr_row.find_all("td")
                                        if len(tr_cols) == 2 and "colhead" not in tr_cols[0].get("class", []):
                                            file_list.append(tr_cols[0].text.strip())
                                    
                                    if file_list:
                                        ficha_data["raw_filenames"] = json.dumps(file_list, ensure_ascii=False)
                                except Exception as e:
                                    logger.warning(f"⚠️ [INDEXER] [{self.name}] No se pudo extraer filelist AJAX para {source_guid}: {e}")

                            enriched_title = f"{original_title} {ficha_data['extra_info']}".strip()
                            
                            poster_url = None
                            if ficha_data.get("forum_url"):
                                poster_url = await self.get_poster(ficha_data["forum_url"], cookie_string)
                                
                            results.append({
                                "guid": guid,
                                "source_guid": source_guid,
                                "original_title": original_title,
                                "title": enriched_title,
                                "description": ficha_data['description'],
                                "poster_url": poster_url,
                                "size_bytes": size_bytes,
                                "seeders": seeders,
                                "leechers": leechers,
                                "publish_date": ficha_data['pub_date'],
                                "freeleech_until": ficha_data['freeleech_until'],
                                "raw_filenames": ficha_data.get('raw_filenames'),
                                "tags": ficha_data.get('tags'),
                                "fansub": formatted_fansub
                            })
                            await asyncio.sleep(1.5)
                        except Exception as e:
                            deep_scrape_errors += 1
                            logger.error(f"❌ [INDEXER] [{self.name}] Error raspando ficha {torrent_id}: {e}")

            logger.debug(
                f"✅ [INDEXER] [{self.name}] Búsqueda completada para '{query}': "
                f"resultados={len(results)}, cache={cache_hits}, cache_sin_poster={cache_without_poster}, "
                f"raspado_profundo={deep_scraped}, errores_raspado={deep_scrape_errors}."
            )
            return results
            
        except Exception as e:
            logger.error(f"❌ [INDEXER] [{self.name}] Error buscando '{query}': {e}")
            return []

    # ------------------------------------------------------------
    # Obtiene la portada del hilo del foro asociado a un torrent de
    # Union Fansub para mostrarla en la biblioteca de Kitsunarr.
    # ------------------------------------------------------------
    async def get_poster(self, url: str, cookie_string: str) -> Optional[str]:
        if not url: return None
        if url.startswith("//"): url = f"https:{url}"

        # ------------------------------------------------------------
        # Normaliza URLs de imagen encontradas en el foro para que la
        # portada pueda proxificarse o mostrarse desde Kitsunarr.
        # ------------------------------------------------------------
        def _normalize_img_url(img_url: str) -> Optional[str]:
            if not img_url:
                return None
            img_url = img_url.strip()
            if img_url.startswith("//"):
                return f"https:{img_url}"
            if img_url.startswith("http://") or img_url.startswith("https://"):
                return img_url
            return urllib.parse.urljoin(url, img_url)
            
        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "foro.unionfansub.com"

        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=15.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, "lxml")

                portada_div = soup.find("div", class_="portada")
                
                if portada_div and portada_div.has_attr("style"):
                    match = re.search(r"url\(['\"]?(.*?)['\"]?\)", portada_div["style"])
                    if match:
                        return _normalize_img_url(match.group(1))

                portada_img = soup.select_one("div.portada img")
                if portada_img and portada_img.get("src"):
                    return _normalize_img_url(portada_img.get("src"))

                og_img = soup.select_one('meta[property="og:image"]')
                if og_img and og_img.get("content"):
                    return _normalize_img_url(og_img.get("content"))

                first_post_img = soup.select_one(".post_body img, .postcontent img, .post img")
                if first_post_img and first_post_img.get("src"):
                    return _normalize_img_url(first_post_img.get("src"))

                logger.warning(f"⚠️ [INDEXER] [{self.name}] No se encontró portada en el hilo: {url}")
        except Exception as e:
            logger.warning(f"⚠️ [INDEXER] [{self.name}] Error extrayendo portada desde {url}: {e}")
        return None

    # ------------------------------------------------------------
    # Descarga desde Union Fansub el archivo .torrent crudo asociado
    # a una ficha para entregarlo a Sonarr o calcular su info hash.
    # ------------------------------------------------------------
    async def download_torrent(self, guid: str, cookie_string: str) -> bytes:
        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Referer"] = f"https://torrent.unionfansub.com/details.php?id={guid}"

        async with httpx.AsyncClient(follow_redirects=True) as client:
            primary_url = f"https://torrent.unionfansub.com/download.php?torrent={guid}"
            resp = await client.get(primary_url, headers=headers, timeout=15.0)
            resp.raise_for_status()

            if resp.content.startswith(b"d8:announce"):
                return resp.content

            fallback_url = f"https://torrent.unionfansub.com/download.php?id={guid}"
            resp_fallback = await client.get(fallback_url, headers=headers, timeout=15.0)
            resp_fallback.raise_for_status()

            if resp_fallback.content.startswith(b"d8:announce"):
                return resp_fallback.content

            raise Exception("El tracker devolvió un archivo no válido (URL de descarga/cookie/referer).")

