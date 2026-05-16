import re
import time
import json
import urllib.parse
import asyncio
import unicodedata
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
# busqueda paginada, scraping profundo de fichas, portadas, metadatos
# tecnicos y descarga de archivos .torrent.
# ------------------------------------------------------------
class UnionFansubIndexer(BaseIndexer):
    GUID_PREFIX = "UNF"
    TORZNAB_DEADLINE_SECONDS = 90.0
    TORZNAB_DEADLINE_SAFETY_MARGIN_SECONDS = 2.0
    
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
    # con las paginas de Union Fansub.
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
    # Convierte fechas del tracker a RFC822 aceptando formatos
    # absolutos y relativos como "Hoy, HH:MM" y "Ayer, HH:MM".
    # Usa la zona horaria local del sistema donde se ejecuta Kitsunarr.
    # ------------------------------------------------------------
    def _parse_spanish_date_to_rfc(self, date_str: str) -> str:
        meses = {
            "ene": "01", "jan": "01", "enero": "01", "january": "01",
            "feb": "02", "febrero": "02", "february": "02",
            "mar": "03", "marzo": "03", "march": "03",
            "abr": "04", "apr": "04", "abril": "04", "april": "04",
            "may": "05", "mayo": "05",
            "jun": "06", "junio": "06", "june": "06",
            "jul": "07", "julio": "07", "july": "07",
            "ago": "08", "aug": "08", "agosto": "08", "august": "08",
            "sep": "09", "sept": "09", "septiembre": "09", "september": "09",
            "oct": "10", "octubre": "10", "october": "10",
            "nov": "11", "noviembre": "11", "november": "11",
            "dic": "12", "dec": "12", "diciembre": "12", "december": "12",
        }
        raw = (date_str or "").strip()
        now_local = datetime.now().astimezone()
        try:
            rel_match = re.search(r"(?i)\b(hoy|ayer)\b[, ]+(\d{1,2}):(\d{2})", raw)
            if rel_match:
                rel_word = rel_match.group(1).lower()
                hh = int(rel_match.group(2))
                mm = int(rel_match.group(3))
                base_day = now_local.date()
                if rel_word == "ayer":
                    base_day = (now_local - timedelta(days=1)).date()
                dt = datetime(base_day.year, base_day.month, base_day.day, hh, mm, tzinfo=now_local.tzinfo)
                return formatdate(dt.timestamp(), localtime=False, usegmt=True)

            clean_str = raw.replace(",", " ")
            parts = [p for p in clean_str.split() if p]
            month_key = parts[1].lower().strip(".") if len(parts) >= 4 else ""
            month_key = month_key.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
            if len(parts) >= 4 and month_key in meses:
                day = parts[0].zfill(2)
                month = meses[month_key]
                year = parts[2]
                time_str = parts[3]
                dt = datetime.strptime(f"{year}-{month}-{day} {time_str}", "%Y-%m-%d %H:%M")
                dt = dt.replace(tzinfo=now_local.tzinfo)
                return formatdate(dt.timestamp(), localtime=False, usegmt=True)
        except Exception:
            logger.warning(f"[INDEXER] [{self.name}] Fecha no parseable desde tracker: '{raw}'.")
        return formatdate(time.time(), localtime=False, usegmt=True)

    # ------------------------------------------------------------
    # Normaliza etiquetas del tracker para que Kitsunarr pueda leer
    # campos de ficha aunque lleguen con acentos o codificacion rota.
    # ------------------------------------------------------------
    def _normalize_label_text(self, text: str) -> str:
        raw = text or ""
        raw = raw.replace("\u00c3\u00b1", "n").replace("\u00c3\u00ad", "i")
        raw = raw.replace("\u00c3\u00b3", "o").replace("\u00c3\u00a1", "a")
        raw = raw.replace("\u00c3\u00a9", "e").replace("\u00c3\u00ba", "u")
        normalized = unicodedata.normalize("NFKD", raw)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()
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
    # Construye el titulo original de Kitsunarr combinando etiqueta de
    # fansub y nombre del torrent del tracker.
    # ------------------------------------------------------------
    def _build_original_title(self, fansub_label: str, title: str) -> str:
        label = (fansub_label or "").strip()
        if not label:
            return title or "Sin titulo"
        if label.startswith("[") and label.endswith("]"):
            return f"{label} {title}".strip()
        return f"[{label}] {title}".strip()

    # ------------------------------------------------------------
    # Extrae el fansub desde una fila de resultados del tracker para
    # generar titulos consistentes antes del raspado profundo.
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
    # lista de resultados no aporta suficiente informacion.
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
    # Extrae el titulo principal desde la ficha detallada del tracker
    # para que las fichas nuevas se creen con datos de details.php.
    # ------------------------------------------------------------
    def _extract_title_from_details(self, html_content: str) -> Optional[str]:
        soup = BeautifulSoup(html_content, "lxml")
        h1_title = soup.select_one("h1")
        if h1_title:
            title = h1_title.get_text(" ", strip=True)
            if title:
                return title
        return None
    # ------------------------------------------------------------
    # Raspa la ficha detallada de Union Fansub para obtener sinopsis,
    # freeleech, tamano real, fecha, enlace de foro y tags tecnicos.
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
            "tags": None,
            "size_bytes": 0,
            "seeders": 0,
            "leechers": 0
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

        for td in soup.find_all("td"):
            label = self._normalize_label_text(td.get_text(" ", strip=True))
            value_td = td.find_next_sibling("td")
            if not value_td:
                continue

            value = value_td.get_text(" ", strip=True)
            if "tamano" in label:
                bytes_match = re.search(r"\(([\d\.,]+)\s*bytes\)", value, re.IGNORECASE)
                if bytes_match:
                    explicit_bytes = re.sub(r"[^\d]", "", bytes_match.group(1))
                    if explicit_bytes:
                        result["size_bytes"] = int(explicit_bytes)
            elif "anadido" in label:
                result["pub_date"] = self._parse_spanish_date_to_rfc(value)

        if not ficha_table:
            return result

        metadata = []
        tags_list = []
        
        for row in ficha_table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) == 2:
                key = cols[0].text.strip().lower()
                normalized_key = self._normalize_label_text(key)
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

                elif "subtitulo" in normalized_key: 
                    value = value.replace("Castellano", "Espanol (Castellano)").replace("Latino", "Espanol (Latino)")
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
                    
                elif "caracteristica" in normalized_key:
                    if "ordered chapters" in v_lower: tags_list.append("Ordered Chapters")
                    if "softsubs" in v_lower: tags_list.append("Softsubs")
                    if "hardsubs" in v_lower: tags_list.append("Hardsubs")

                elif "descarga directa" in key:
                    link_tag = cols[1].find("a", href=re.compile(r"showthread\.php\?tid="))
                    if link_tag:
                        result["forum_url"] = link_tag["href"]
                elif "pares" in normalized_key:
                    seed_match = re.search(r"(\d+)\s*semilla", v_lower, re.IGNORECASE)
                    leech_match = re.search(r"(\d+)\s*leecher", v_lower, re.IGNORECASE)
                    if seed_match:
                        result["seeders"] = int(seed_match.group(1))
                    if leech_match:
                        result["leechers"] = int(leech_match.group(1))

        if not result["forum_url"]:
            any_thread_link = soup.find("a", href=re.compile(r"showthread\.php\?tid="))
            if any_thread_link:
                result["forum_url"] = any_thread_link.get("href")

        if result["seeders"] == 0 and result["leechers"] == 0:
            cpeerlist = soup.find(id="cpeerlist")
            if cpeerlist:
                peer_text = cpeerlist.get_text(" ", strip=True).lower()
                seed_match = re.search(r"(\d+)\s*semilla", peer_text, re.IGNORECASE)
                leech_match = re.search(r"(\d+)\s*leecher", peer_text, re.IGNORECASE)
                if seed_match:
                    result["seeders"] = int(seed_match.group(1))
                if leech_match:
                    result["leechers"] = int(leech_match.group(1))

        result["extra_info"] = " ".join(metadata)
        
        if tags_list:
            unique_tags = list(dict.fromkeys(tags_list))
            result["tags"] = json.dumps(unique_tags, ensure_ascii=False)


        return result
    
    # ------------------------------------------------------------
    # Convierte el tamano textual del tracker a bytes para que
    # Kitsunarr pueda informar el peso real por Torznab y en la UI,
    # aceptando unidades decimales (KB/MB/GB/TB) y binarias
    # (KiB/MiB/GiB/TiB).
    # ------------------------------------------------------------
    def _parse_size_to_bytes(self, size_str: str) -> int:
        size_str = size_str.replace(",", ".").upper().strip()
        try:
            if "TIB" in size_str: return int(float(size_str.replace("TIB", "").strip()) * 1024**4)
            elif "GIB" in size_str: return int(float(size_str.replace("GIB", "").strip()) * 1024**3)
            elif "MIB" in size_str: return int(float(size_str.replace("MIB", "").strip()) * 1024**2)
            elif "KIB" in size_str: return int(float(size_str.replace("KIB", "").strip()) * 1024)
            elif "TB" in size_str: return int(float(size_str.replace("TB", "").strip()) * 1024**4)
            elif "GB" in size_str: return int(float(size_str.replace("GB", "").strip()) * 1024**3)
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
    # Inicia sesion en Union Fansub desde Kitsunarr y devuelve la
    # cookie necesaria para busquedas, fichas, portadas y descargas.
    # ------------------------------------------------------------
    async def login(self, username: str, password: str) -> Optional[str]:
        tracker_url = "https://torrent.unionfansub.com/index.php"
        login_url = "https://foro.unionfansub.com/member.php"
        headers = self._get_base_headers()

        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=30.0) as client:
                logger.info(f"[INDEXER] [{self.name}] Visitando tracker para obtener cookies iniciales.")
                await client.get(tracker_url)
                
                payload = {
                    "action": "do_login", "url": "//torrent.unionfansub.com/",
                    "quick_login": "1", "quick_username": username,
                    "quick_password": password, "submit": "Iniciar sesion", "quick_remember": "yes"
                }
                
                client.headers.update({
                    "Origin": "https://torrent.unionfansub.com",
                    "Referer": "https://torrent.unionfansub.com/",
                    "Content-Type": "application/x-www-form-urlencoded"
                })
                
                await client.post(login_url, data=payload)
                
                full_cookie_string = "; ".join([f"{name}={value}" for name, value in client.cookies.items()])
                
                if "mybbuser" in full_cookie_string:
                    logger.info(f"[INDEXER] [{self.name}] Login exitoso. Cookie 'mybbuser' generada.")
                    return full_cookie_string
                return None
        except Exception as e:
            logger.error(f"[INDEXER] [{self.name}] Error de red durante el login: {e}")
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
    # Extrae de una pagina de busqueda de Union Fansub las filas de
    # torrents con ID, enlace de ficha y titulo de referencia.
    # El conteo de pares no se toma desde esta lista.
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
            title = title_tag.text.strip() if title_tag else "Sin titulo"

            formatted_fansub = self._extract_fansub_from_list_row(cols[1], title)
            original_title = self._build_original_title(formatted_fansub, title)

            details_link = cols[1].find("a", href=True)
            torrent_id_match = re.search(r"id=(\d+)", details_link["href"]) if details_link else None
            torrent_id = torrent_id_match.group(1) if torrent_id_match else ""
            details_url = urllib.parse.urljoin("https://torrent.unionfansub.com/", details_link["href"]) if details_link else ""
            if not torrent_id:
                continue

            parsed_rows.append({
                "guid": f"{self.GUID_PREFIX}-{torrent_id}",
                "source_guid": torrent_id,
                "fansub": formatted_fansub,
                "raw_title": title,
                "original_title": original_title,
                "details_url": details_url,
            })

        return parsed_rows

    # ------------------------------------------------------------
    # Detecta si la busqueda actual de Union Fansub tiene una pagina
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
    # Comprueba si se ha alcanzado la ventana maxima de respuesta y
    # fuerza salida temprana para devolver resultados parciales validos.
    # ------------------------------------------------------------
    def _is_deadline_reached(self, started_at: float, deadline_seconds: Optional[float]) -> bool:
        if not deadline_seconds or deadline_seconds <= 0:
            return False
        effective_deadline = max(1.0, deadline_seconds - self.TORZNAB_DEADLINE_SAFETY_MARGIN_SECONDS)
        return (time.monotonic() - started_at) >= effective_deadline

    # ------------------------------------------------------------
    # Ejecuta el scraping de Union Fansub con soporte de deadline
    # opcional para cortar por tiempo y devolver resultados reales
    # parciales antes de que Sonarr marque timeout.
    # ------------------------------------------------------------
    async def _search_internal(self, query: str, cookie_string: str, deadline_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        encoded_query = urllib.parse.quote_plus(query) if query else ""
        categories = "&c13=1&c1=1&c2=1&c3=1"
        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "torrent.unionfansub.com"
        started_at = time.monotonic()
        timed_out = False
        deadline_mode = bool(deadline_seconds and deadline_seconds > 0)

        logger.info(
            f"[INDEXER] [{self.name}] Inicio busqueda: query='{query}', "
            f"modo_deadline={'si' if deadline_mode else 'no'}, "
            f"limite={deadline_seconds if deadline_mode else 'sin limite'}s."
        )

        results = []
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
                parsed_rows = []
                seen_page_guids = set()

                has_search_query = bool(query and query.strip())
                page = 0

                while True:
                    if self._is_deadline_reached(started_at, deadline_seconds):
                        timed_out = True
                        logger.warning(
                            f"[INDEXER] [{self.name}] Corte por tiempo en paginacion: "
                            f"query='{query}', pagina={page}, filas_acumuladas={len(parsed_rows)}."
                        )
                        break
                    url = f"https://torrent.unionfansub.com/browse.php?search={encoded_query}&incldead=0{categories}&page={page}"
                    logger.info(f"[INDEXER] [{self.name}] URL busqueda tracker: {url}")
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 302:
                        raise Exception("El tracker rechazo la cookie (Redireccion 302).")
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
                        f"[INDEXER] [{self.name}] Pagina {page}: filas={len(page_rows)}, "
                        f"nuevas={new_rows}, siguiente={'si' if has_next else 'no'}."
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
                    if self._is_deadline_reached(started_at, deadline_seconds):
                        timed_out = True
                        logger.warning(
                            f"[INDEXER] [{self.name}] Corte por tiempo en raspado profundo: "
                            f"query='{query}', procesados={len(results)}, pendientes={len(parsed_rows) - len(results)}."
                        )
                        break
                    guid = item["guid"]
                    source_guid = item["source_guid"]
                    incoming_fansub = item["fansub"]
                    base_title = item["raw_title"]
                    original_title = item["original_title"]
                    details_url = item.get("details_url") or f"https://torrent.unionfansub.com/details.php?id={source_guid}&hit=1"
                    db_torrent = cached_by_guid.get(guid)

                    if db_torrent:
                        cache_hits += 1
                        if not db_torrent.poster_url:
                            cache_without_poster += 1
                        cached_original_title = db_torrent.original_title or original_title
                        results.append({
                            "guid": db_torrent.guid,
                            "source_guid": db_torrent.source_guid or source_guid,
                            "original_title": cached_original_title,
                            "title": db_torrent.enriched_title or cached_original_title,
                            "description": db_torrent.description,
                            "poster_url": db_torrent.poster_url,
                            "size_bytes": db_torrent.size_bytes or 0,
                            "seeders": int(db_torrent.peers_seeds or 0),
                            "leechers": int(db_torrent.peers_leechs or 0),
                            "publish_date": db_torrent.pub_date,
                            "freeleech_until": db_torrent.freeleech_until,
                            "raw_filenames": db_torrent.raw_filenames,
                            "tags": db_torrent.tags,
                            "fansub": db_torrent.fansub_name or incoming_fansub
                        })
                    else:
                        deep_scraped += 1
                        
                        try:
                            details_resp = await client.get(details_url, headers=headers)
                            ficha_data = self._parse_ficha_metadata(details_resp.text)
                            detail_title = self._extract_title_from_details(details_resp.text) or base_title
                            detail_fansub = self._extract_fansub_from_details(details_resp.text)
                            formatted_fansub = detail_fansub or incoming_fansub or self._format_fansub_label("Union Fansub")
                            original_title = self._build_original_title(formatted_fansub, detail_title)
                            if not ficha_data.get("size_bytes"):
                                logger.warning(
                                    f"[INDEXER] [{self.name}] Tamano no disponible en details.php para {source_guid}; "
                                    "se usa el tamaño de la lista de búsqueda."
                                )
                            
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
                                    logger.warning(f"[INDEXER] [{self.name}] No se pudo extraer filelist AJAX para {source_guid}: {e}")

                            enriched_title = f"{original_title} {ficha_data['extra_info']}".strip()
                            
                            poster_url = None
                            if ficha_data.get("forum_url"):
                                poster_url = await self.get_poster(ficha_data["forum_url"], cookie_string)

                            effective_size_bytes = ficha_data.get("size_bytes") or item.get("size_bytes", 0)
                            effective_seeders = int(ficha_data.get("seeders") or 0)
                            effective_leechers = int(ficha_data.get("leechers") or 0)

                            results.append({
                                "guid": guid,
                                "source_guid": source_guid,
                                "original_title": original_title,
                                "title": enriched_title,
                                "description": ficha_data['description'],
                                "poster_url": poster_url,
                                "size_bytes": effective_size_bytes,
                                "seeders": effective_seeders,
                                "leechers": effective_leechers,
                                "publish_date": ficha_data['pub_date'],
                                "freeleech_until": ficha_data['freeleech_until'],
                                "raw_filenames": ficha_data.get('raw_filenames'),
                                "tags": ficha_data.get('tags'),
                                "fansub": formatted_fansub
                            })
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            deep_scrape_errors += 1
                            logger.error(f"[INDEXER] [{self.name}] Error raspando ficha {source_guid}: {e}")

            logger.debug(
                f"[INDEXER] [{self.name}] Busqueda completada para '{query}': "
                f"resultados={len(results)}, cache={cache_hits}, cache_sin_poster={cache_without_poster}, "
                f"raspado_profundo={deep_scraped}, errores_raspado={deep_scrape_errors}, "
                f"corte_por_tiempo={'si' if timed_out else 'no'}, duracion={time.monotonic() - started_at:.2f}s."
            )
            return results
            
        except Exception as e:
            logger.error(f"[INDEXER] [{self.name}] Error buscando '{query}': {e}")
            return []

    # ------------------------------------------------------------
    # Ejecuta busqueda estandar sin limite de tiempo especifico.
    # ------------------------------------------------------------
    async def search(self, query: str, cookie_string: str) -> List[Dict[str, Any]]:
        return await self._search_internal(query, cookie_string, deadline_seconds=None)

    # ------------------------------------------------------------
    # Ejecuta busqueda con deadline fijo orientado a Torznab/Sonarr
    # para garantizar respuesta antes de timeout del cliente.
    # ------------------------------------------------------------
    async def search_with_deadline(self, query: str, cookie_string: str) -> List[Dict[str, Any]]:
        return await self._search_internal(query, cookie_string, deadline_seconds=self.TORZNAB_DEADLINE_SECONDS)

    # ------------------------------------------------------------
    # Rehidrata una ficha concreta desde su URL de detalles en Union
    # Fansub y devuelve metadatos actualizados para la cache local.
    # ------------------------------------------------------------
    async def rehydrate_torrent(self, source_guid: str, cookie_string: str, base_title: str, current_original_title: str = "") -> Optional[Dict[str, Any]]:
        if not source_guid:
            return None

        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "torrent.unionfansub.com"
        details_url = f"https://torrent.unionfansub.com/details.php?id={source_guid}&hit=1"

        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
                details_resp = await client.get(details_url, headers=headers)
                if details_resp.status_code == 302:
                    raise Exception("El tracker rechazo la cookie (Redireccion 302).")
                details_resp.raise_for_status()

                ficha_data = self._parse_ficha_metadata(details_resp.text)
                detail_title = self._extract_title_from_details(details_resp.text)
                detail_fansub = self._extract_fansub_from_details(details_resp.text)
                if not ficha_data.get("size_bytes"):
                    logger.warning(
                        f"[INDEXER] [{self.name}] Tamano no disponible en details.php durante rehidratacion para {source_guid}."
                    )

                formatted_fansub = detail_fansub or self._format_fansub_label("Union Fansub")
                safe_base_title = (detail_title or "").strip() or (base_title or "").strip() or (current_original_title or "").strip() or f"Torrent {source_guid}"
                rebuilt_original_title = self._build_original_title(formatted_fansub, safe_base_title)
                enriched_title = f"{rebuilt_original_title} {ficha_data['extra_info']}".strip()

                if not ficha_data.get("raw_filenames"):
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

                poster_url = None
                if ficha_data.get("forum_url"):
                    poster_url = await self.get_poster(ficha_data["forum_url"], cookie_string)

                return {
                    "source_guid": source_guid,
                    "fansub": formatted_fansub,
                    "original_title": rebuilt_original_title,
                    "title": enriched_title,
                    "description": ficha_data.get("description"),
                    "poster_url": poster_url,
                    "size_bytes": ficha_data.get("size_bytes", 0),
                    "seeders": int(ficha_data.get("seeders") or 0),
                    "leechers": int(ficha_data.get("leechers") or 0),
                    "publish_date": ficha_data.get("pub_date"),
                    "freeleech_until": ficha_data.get("freeleech_until"),
                    "raw_filenames": ficha_data.get("raw_filenames"),
                    "tags": ficha_data.get("tags"),
                }

        except Exception as e:
            logger.error(f"[INDEXER] [{self.name}] Error rehidratando ficha {source_guid}: {e}")
            return None

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

                logger.warning(f"[INDEXER] [{self.name}] No se encontro portada en el hilo: {url}")
        except Exception as e:
            logger.warning(f"[INDEXER] [{self.name}] Error extrayendo portada desde {url}: {e}")
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

            raise Exception("El tracker devolvio un archivo no valido (URL de descarga/cookie/referer).")
