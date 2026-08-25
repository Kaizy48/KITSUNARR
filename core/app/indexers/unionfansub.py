import re
import time
import urllib.parse
import asyncio
import os
import unicodedata
from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import Decimal, InvalidOperation
from email.utils import format_datetime
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import bencodepy
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from core.database.engine import engine
from core.database.models import TorrentCache
from core.app.logger import logger
from core.app.indexers.base import BaseIndexer
from core.app.indexers.snapshots import (
    InvalidTrackerSnapshot,
    TrackerFileEntry,
    TrackerScreenshot,
    TrackerSnapshot,
)


SCRAPE_REVISION = 1
TRACKER_METADATA_VERSION = 1
DETAILS_BASE_URL = "https://torrent.unionfansub.com/"
ALLOWED_TRACKER_HOSTS = frozenset({
    "torrent.unionfansub.com",
    "foro.unionfansub.com",
    "galeria.unionfansub.com",
})
DEFAULT_TRACKER_TIMEZONE = "Europe/Madrid"
DOWNLOAD_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_DOWNLOAD_REDIRECTS = 3


class TorrentDownloadError(RuntimeError):
    """Indica un fallo seguro y diagnosticable al obtener metainfo del tracker."""

# ------------------------------------------------------------
# Indexador nativo de Kitsunarr para Union Fansub. Gestiona login,
# busqueda paginada, scraping profundo de fichas, portadas, metadatos
# tecnicos y descarga de archivos .torrent.
# ------------------------------------------------------------
class UnionFansubIndexer(BaseIndexer):
    GUID_PREFIX = "UNF"
    SCRAPE_REVISION = SCRAPE_REVISION
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
    # Resuelve la zona IANA usada para interpretar fechas del tracker.
    # Lee TZ del entorno y usa Europe/Madrid como fallback coherente con
    # Union Fansub; no modifica estado ni hace red. Devuelve UTC solo si
    # tampoco existe la zona fallback y registra el nombre inválido sin
    # incluir credenciales; depende de zoneinfo y de la base tz del host.
    # ------------------------------------------------------------
    def _get_tracker_timezone(self) -> tzinfo:
        configured_name = (os.getenv("TZ") or DEFAULT_TRACKER_TIMEZONE).strip()
        configured_name = configured_name or DEFAULT_TRACKER_TIMEZONE
        try:
            return ZoneInfo(configured_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                f"[INDEXER] [{self.name}] Zona horaria '{configured_name}' no disponible; "
                f"se usará {DEFAULT_TRACKER_TIMEZONE}."
            )
        try:
            return ZoneInfo(DEFAULT_TRACKER_TIMEZONE)
        except ZoneInfoNotFoundError:
            return timezone.utc

    # ------------------------------------------------------------
    # Convierte una fecha del tracker a datetime UTC con zona horaria.
    # Acepta formatos absolutos y relativos Hoy/Ayer, interpreta TZ con
    # una ZoneInfo IANA que conserva el DST histórico y devuelve None —sin
    # inventar now— si no es válida. No propaga errores de parseo, pero registra
    # un warning saneado como efecto; depende de _get_tracker_timezone.
    # ------------------------------------------------------------
    def _parse_spanish_datetime_utc(self, date_str: str) -> Optional[datetime]:
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
        if not raw:
            return None
        tracker_timezone = self._get_tracker_timezone()
        now_local = datetime.now(tracker_timezone)
        try:
            rel_match = re.search(r"(?i)\b(hoy|ayer)\b[, ]+(\d{1,2}):(\d{2})", raw)
            if rel_match:
                rel_word = rel_match.group(1).lower()
                hh = int(rel_match.group(2))
                mm = int(rel_match.group(3))
                base_day = now_local.date()
                if rel_word == "ayer":
                    base_day = (now_local - timedelta(days=1)).date()
                dt = datetime(base_day.year, base_day.month, base_day.day, hh, mm, tzinfo=tracker_timezone)
                return dt.astimezone(timezone.utc)

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
                dt = dt.replace(tzinfo=tracker_timezone)
                return dt.astimezone(timezone.utc)
        except Exception:
            logger.warning(f"[INDEXER] [{self.name}] Fecha no parseable desde tracker: '{raw}'.")
        return None

    # ------------------------------------------------------------
    # Mantiene el puente RFC822 legacy: recibe el texto de fecha remoto y
    # devuelve un string RFC822 UTC o None si no puede observarse. No hace
    # red ni muta estado, pero _parse_spanish_datetime_utc puede registrar
    # un warning de parseo. Los errores de parseo se normalizan a None y un
    # error inesperado de format_datetime se propaga. Depende de ese parser
    # tipado y de email.utils.format_datetime; nunca inventa la hora actual.
    # ------------------------------------------------------------
    def _parse_spanish_date_to_rfc(self, date_str: str) -> Optional[str]:
        parsed = self._parse_spanish_datetime_utc(date_str)
        if parsed is None:
            return None
        return format_datetime(parsed, usegmt=True)

    # ------------------------------------------------------------
    # Formatea value para consumidores legacy y devuelve RFC822 UTC o None;
    # un datetime naive se interpreta como UTC por compatibilidad SQLite.
    # No muta el valor ni hace I/O o logging. TypeError/ValueError/OverflowError
    # de una entrada datetime incompatible o del formateo se propagan. Depende
    # de datetime.astimezone/replace, timezone.utc y format_datetime; el DTO
    # principal conserva por separado el datetime tipado.
    # ------------------------------------------------------------
    def _format_datetime_to_rfc(self, value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return format_datetime(normalized, usegmt=True)

    # ------------------------------------------------------------
    # Normaliza el texto de una etiqueta y devuelve una clave canónica aunque
    # contenga acentos, mojibake o U+FFFD. No muta la entrada ni hace I/O o
    # logging. Una entrada no textual incompatible puede propagar AttributeError
    # o TypeError; no se silencian errores de regex/Unicode. Depende de re,
    # unicodedata y los aliases locales, y alimenta _iter_label_value_rows y
    # la extracción técnica de _parse_ficha_snapshot.
    # ------------------------------------------------------------
    def _normalize_label_text(self, text: str) -> str:
        raw = text or ""
        raw = raw.replace("\u00c3\u00b1", "n").replace("\u00c3\u00ad", "i")
        raw = raw.replace("\u00c3\u00b3", "o").replace("\u00c3\u00a1", "a")
        raw = raw.replace("\u00c3\u00a9", "e").replace("\u00c3\u00ba", "u")
        raw = re.sub(r"\ufffd+", "", raw)
        normalized = unicodedata.normalize("NFKD", raw)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = normalized.lower()
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized.strip()

        suffix = ":" if normalized.endswith(":") else ""
        skeleton = normalized.rstrip(":").strip()
        replacement_aliases = {
            "descripcin": "descripcion",
            "ltima semilla": "ultima semilla",
            "tamao": "tamano",
            "aadido": "anadido",
        }
        return replacement_aliases.get(skeleton, skeleton) + suffix
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
    # Sanea una URL remota obtenida del HTML antes de guardarla o
    # solicitarla. Recibe base, hosts y parámetros permitidos,
    # devuelve HTTPS sin credenciales/fragmento o None; no hace I/O y
    # protege a screenshots, foro y portada frente a destinos ajenos.
    # ------------------------------------------------------------
    def _sanitize_remote_url(
        self,
        raw_url: Optional[str],
        *,
        base_url: str = DETAILS_BASE_URL,
        allowed_hosts: frozenset[str] = ALLOWED_TRACKER_HOSTS,
        allowed_query_keys: frozenset[str] = frozenset(),
    ) -> Optional[str]:
        if not raw_url or len(raw_url) > 2048:
            return None

        try:
            absolute = urllib.parse.urljoin(base_url, raw_url.strip())
            parsed = urllib.parse.urlparse(absolute)
            hostname = (parsed.hostname or "").lower()
            if parsed.scheme.lower() != "https" or hostname not in allowed_hosts:
                return None
            if parsed.username or parsed.password or parsed.port not in (None, 443):
                return None
            if not parsed.path or len(parsed.path) > 1024:
                return None

            sanitized_query = []
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
                if key not in allowed_query_keys or len(value) > 200:
                    continue
                sanitized_query.append((key, value))

            return urllib.parse.urlunparse((
                "https",
                hostname,
                parsed.path,
                "",
                urllib.parse.urlencode(sanitized_query),
                "",
            ))
        except (ValueError, UnicodeError):
            return None

    # ------------------------------------------------------------
    # Recorre un BeautifulSoup recibido, selecciona filas de al menos dos
    # celdas y devuelve pares (etiqueta normalizada, nodo de valor). No
    # modifica el DOM ni hace I/O o logging. Errores de una entrada que no
    # implemente la API BeautifulSoup se propagan; el HTML irregular solo se
    # omite. Depende de find_all/get_text y de _normalize_label_text, y su
    # salida permite a _parse_ficha_snapshot distinguir presencia de ausencia.
    # ------------------------------------------------------------
    def _iter_label_value_rows(self, soup: BeautifulSoup) -> list[tuple[str, Any]]:
        rows = []
        for row in soup.find_all("tr"):
            cols = row.find_all(["th", "td"], recursive=False)
            if len(cols) < 2:
                continue
            label = self._normalize_label_text(cols[0].get_text(" ", strip=True))
            if label:
                rows.append((label, cols[1]))
        return rows

    # ------------------------------------------------------------
    # Detecta en el BeautifulSoup recibido formularios/marcadores de login y
    # devuelve bool para distinguir autenticación de un HTTP 200 engañoso.
    # No muta el DOM ni hace red, DB o logging. Errores de nodos incompatibles
    # con select_one/find_all/get/get_text se propagan. Depende de los selectores
    # BS4 y la usan _is_authenticated_tracker_page, _validate_details_page,
    # _parse_filelist_response y get_poster.
    # ------------------------------------------------------------
    def _looks_like_login_page(self, soup: BeautifulSoup) -> bool:
        if soup.select_one('input[type="password"], input[name="quick_password"]'):
            return True
        for form in soup.find_all("form", action=True):
            action = str(form.get("action") or "").lower()
            if "member.php" in action and "login" in form.get_text(" ", strip=True).lower():
                return True
        return False

    # ------------------------------------------------------------
    # Comprueba el HTML recibido y devuelve bool si combina chrome autenticado
    # con resultados o marcador vacío, rechazando login HTTP 200. Construye un
    # DOM local pero no hace red, DB, mutaciones externas ni logging. Markup
    # vacío/desconocido devuelve False; errores inesperados de BeautifulSoup,
    # lxml o selectores se propagan. Depende de BeautifulSoup, re y
    # _looks_like_login_page; la usan test_connection y _search_internal.
    # ------------------------------------------------------------
    def _is_authenticated_tracker_page(self, html_content: str) -> bool:
        soup = BeautifulSoup(html_content or "", "lxml")
        if self._looks_like_login_page(soup):
            return False
        has_browse_link = soup.find("a", href=re.compile(r"(?:^|/)browse\.php", re.IGNORECASE)) is not None
        has_results_contract = soup.find("table", class_="tlist") is not None
        has_empty_marker = "no se ha encontrado nada" in soup.get_text(" ", strip=True).lower()
        return has_browse_link and (has_results_contract or has_empty_marker)

    # ------------------------------------------------------------
    # Extrae del soup recibido el primer GUID numérico verificable de enlaces
    # download/filelist/peerlist y devuelve str o None. No confía en sesión,
    # muta el DOM ni hace I/O o logging. URLs sin query/ID se omiten; errores
    # inesperados de nodos BS4 o de urllib.parse se propagan. Depende de re,
    # find_all, urlparse y parse_qs; _validate_details_page compara su salida
    # con la identidad solicitada.
    # ------------------------------------------------------------
    def _extract_source_guid_from_details(self, soup: BeautifulSoup) -> Optional[str]:
        link_patterns = (
            (r"download\.php", "torrent"),
            (r"filelist\.php", "id"),
            (r"peerlist\.php", "id"),
        )
        for href_pattern, query_key in link_patterns:
            for link in soup.find_all("a", href=re.compile(href_pattern, re.IGNORECASE)):
                parsed = urllib.parse.urlparse(str(link.get("href") or ""))
                values = urllib.parse.parse_qs(parsed.query).get(query_key, [])
                if values and str(values[0]).isdigit():
                    return str(values[0])
        return None

    # ------------------------------------------------------------
    # Valida una ficha details a partir de soup y GUID esperado opcional,
    # y devuelve (GUID observado, título, table.ficha). No muta el DOM ni
    # hace red, DB o logging. Lanza InvalidTrackerSnapshot ante login HTTP
    # 200, estructura mínima ausente, GUID esperado inválido o identidad
    # discordante; errores inesperados del DOM se propagan. Depende de
    # _looks_like_login_page, _extract_source_guid_from_details y selectores BS4.
    # ------------------------------------------------------------
    def _validate_details_page(
        self,
        soup: BeautifulSoup,
        expected_source_guid: Optional[str],
    ) -> tuple[str, str, Any]:
        if self._looks_like_login_page(soup):
            raise InvalidTrackerSnapshot("El tracker devolvió una página de login en lugar de la ficha.")

        title_tag = soup.select_one("h1")
        ficha_table = soup.find("table", class_="ficha")
        observed_guid = self._extract_source_guid_from_details(soup)
        tracker_title = title_tag.get_text(" ", strip=True) if title_tag else ""

        if not tracker_title or ficha_table is None or observed_guid is None:
            raise InvalidTrackerSnapshot("La respuesta no contiene la estructura mínima de details.php.")
        if expected_source_guid is not None:
            expected = str(expected_source_guid).strip()
            if not expected.isdigit() or expected != observed_guid:
                raise InvalidTrackerSnapshot(
                    f"La identidad de la ficha no coincide con el torrent solicitado ({expected or 'vacío'})."
                )
        return observed_guid, tracker_title, ficha_table

    # ------------------------------------------------------------
    # Convierte un tamaño humano del tracker a bytes binarios mediante
    # Decimal. Recibe texto con B/KB/MB/GB/TB o variantes IEC, no hace
    # I/O y devuelve None si no hay una medida válida; cero solo se
    # devuelve cuando fue observado explícitamente como cero.
    # ------------------------------------------------------------
    def _parse_size_to_bytes(self, size_str: str) -> Optional[int]:
        match = re.search(
            r"(?<![\d.,])(\d+(?:[\.,]\d+)?)\s*(B|BYTES?|KIB|MIB|GIB|TIB|KB|MB|GB|TB)\b",
            (size_str or "").upper(),
            re.IGNORECASE,
        )
        if not match:
            return None

        factors = {
            "B": 1,
            "BYTE": 1,
            "BYTES": 1,
            "KB": 1024,
            "KIB": 1024,
            "MB": 1024 ** 2,
            "MIB": 1024 ** 2,
            "GB": 1024 ** 3,
            "GIB": 1024 ** 3,
            "TB": 1024 ** 4,
            "TIB": 1024 ** 4,
        }
        try:
            amount = Decimal(match.group(1).replace(",", "."))
            if amount < 0:
                return None
            return int(amount * factors[match.group(2).upper()])
        except (InvalidOperation, KeyError, ValueError):
            return None

    # ------------------------------------------------------------
    # Recibe el texto de tamaño y devuelve (bytes, origen), priorizando el
    # contador exacto y usando el humano solo como fallback; ausencia produce
    # (None, None), nunca un centinela. No muta entradas ni hace I/O/logging.
    # TypeError de texto incompatible o ValueError de una conversión exacta
    # extrema se propagan; el fallback normaliza sus propios errores a None.
    # Depende de re, int y _parse_size_to_bytes.
    # ------------------------------------------------------------
    def _parse_size_field(self, value: str) -> tuple[Optional[int], Optional[str]]:
        exact = re.search(r"\(([\d\.,\s]+)\s*bytes?\)", value or "", re.IGNORECASE)
        if exact:
            digits = re.sub(r"[^\d]", "", exact.group(1))
            if digits:
                return int(digits), "details_exact_bytes"

        human_size = self._parse_size_to_bytes(value)
        if human_size is not None:
            return human_size, "details_human_binary"
        return None, None

    # ------------------------------------------------------------
    # Convierte el texto recibido a un entero decimal o devuelve None cuando
    # no contiene dígitos, preservando la diferencia entre ausencia y cero.
    # No muta la entrada ni hace I/O o logging. TypeError de una entrada no
    # textual y ValueError de int —por ejemplo un número fuera del límite de
    # dígitos de Python— se propagan. Depende de re.sub/re.search e int y lo
    # usan los parsers de estadísticas, peers y file_count.
    # ------------------------------------------------------------
    def _parse_tracker_int(self, value: str) -> Optional[int]:
        match = re.search(r"\d[\d\.,\s]*", value or "")
        if not match:
            return None
        clean = re.sub(r"[^\d]", "", match.group(0))
        return int(clean) if clean else None

    # ------------------------------------------------------------
    # Extrae de la celda HTML recibida capturas ordenadas/deduplicadas y
    # devuelve (tupla de TrackerScreenshot, lista de warnings). No descarga
    # imágenes, muta el DOM ni hace logging; una URL inválida o más de 24
    # elementos queda indicada en warnings para que el caller preserve la
    # colección anterior. InvalidTrackerSnapshot del DTO y errores de un nodo
    # HTML incompatible se propagan. Depende de _sanitize_remote_url,
    # selectores BS4 y TrackerScreenshot.
    # ------------------------------------------------------------
    def _parse_screenshots(self, value_cell: Any) -> tuple[tuple[TrackerScreenshot, ...], list[str]]:
        screenshots = []
        warnings = []
        seen = set()
        images = value_cell.find_all("img", limit=25)
        if len(images) > 24:
            warnings.append("La ficha supera 24 capturas y la colección quedó incompleta.")
        for image in images[:24]:
            raw_thumbnail_url = image.get("src")
            thumbnail_url = self._sanitize_remote_url(
                raw_thumbnail_url,
                allowed_hosts=frozenset({"galeria.unionfansub.com"}),
            )
            parent_link = image.find_parent("a", href=True)
            raw_gallery_url = parent_link.get("href") if parent_link else None
            gallery_url = self._sanitize_remote_url(
                raw_gallery_url,
                allowed_hosts=frozenset({"galeria.unionfansub.com"}),
                allowed_query_keys=frozenset({"id"}),
            )
            if thumbnail_url is None:
                warnings.append("Se ignoró una captura con URL no permitida.")
                continue
            if raw_gallery_url and gallery_url is None:
                warnings.append("Se ignoró el enlace de galería no permitido de una captura.")
            key = (thumbnail_url, gallery_url)
            if key in seen:
                continue
            seen.add(key)
            alt = (image.get("alt") or "").strip()[:300] or None
            screenshots.append(TrackerScreenshot(thumbnail_url, gallery_url, alt))
        return tuple(screenshots), warnings

    # ------------------------------------------------------------
    # Lee el contenedor HTML recibido y devuelve (tupla de TrackerFileEntry o
    # None, indicador de truncado), conservando ruta, orden y tamaño nullable.
    # No muta el DOM ni hace red, DB o logging; deduplica en memoria y detecta
    # la entrada 5001. Errores de nodos incompatibles y
    # InvalidTrackerSnapshot/errores de construcción se propagan. Depende de
    # selectores BS4, _parse_size_to_bytes y TrackerFileEntry; su caller decide
    # si una colección puede marcarse observada.
    # ------------------------------------------------------------
    def _parse_file_entries_container(
        self,
        container: Any,
    ) -> tuple[Optional[tuple[TrackerFileEntry, ...]], bool]:
        entries = []
        seen_paths = set()
        for row in container.find_all("tr"):
            cols = row.find_all("td", recursive=False)
            if len(cols) < 2 or "colhead" in cols[0].get("class", []):
                continue
            path = cols[0].get_text(" ", strip=True)
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            size_bytes = self._parse_size_to_bytes(cols[1].get_text(" ", strip=True))
            entries.append(TrackerFileEntry(path=path[:4096], size_bytes=size_bytes))
            if len(entries) > 5000:
                return tuple(entries[:5000]), True
        return (tuple(entries) if entries else None), False

    # ------------------------------------------------------------
    # Valida el HTML AJAX y el contador esperado opcional, y devuelve
    # (tupla completa de TrackerFileEntry o None, warning o None) sin exigir
    # el .torrent. No hace red, logging ni muta entradas. Login HTTP 200,
    # markup desconocido, truncado o conteo distinto se expresan como warning;
    # errores de BeautifulSoup/DTO no contemplados se propagan. Depende de
    # _looks_like_login_page y _parse_file_entries_container.
    # ------------------------------------------------------------
    def _parse_filelist_response(
        self,
        html_content: str,
        expected_file_count: Optional[int] = None,
    ) -> tuple[Optional[tuple[TrackerFileEntry, ...]], Optional[str]]:
        soup = BeautifulSoup(html_content or "", "lxml")
        if self._looks_like_login_page(soup):
            return None, "El filelist devolvió una página de login."
        entries, was_truncated = self._parse_file_entries_container(soup)
        if was_truncated:
            return None, "El filelist AJAX supera 5000 archivos y quedó incompleto."
        if entries is None and expected_file_count == 0:
            return tuple(), None
        if entries is None:
            return None, "El filelist AJAX no contenía filas reconocibles."
        if expected_file_count is not None and len(entries) != expected_file_count:
            return None, (
                "El filelist AJAX quedó incompleto: "
                f"se anunciaron {expected_file_count} archivos y se obtuvieron {len(entries)}."
            )
        return entries, None

    # ------------------------------------------------------------
    # Construye y devuelve TrackerSnapshot desde HTML, GUID esperado opcional
    # y observed_at opcional (usa el reloj UTC si falta). No hace red/DB ni
    # muta el HTML, pero el parser de fecha/zona puede registrar warnings.
    # Conserva presencia, cero y vacíos, y marca partial/warnings ante datos
    # útiles incompletos. Propaga InvalidTrackerSnapshot y errores inesperados
    # de DOM/DTO. Depende de los validadores de details, filas, fechas, tamaños,
    # URLs, capturas y de TrackerSnapshot para imponer el contrato final.
    # ------------------------------------------------------------
    def _parse_ficha_snapshot(
        self,
        html_content: str,
        expected_source_guid: Optional[str] = None,
        observed_at: Optional[datetime] = None,
    ) -> TrackerSnapshot:
        soup = BeautifulSoup(html_content or "", "lxml")
        source_guid, tracker_title, ficha_table = self._validate_details_page(soup, expected_source_guid)
        scraped_at = observed_at or datetime.now(timezone.utc)
        if scraped_at.tzinfo is None:
            scraped_at = scraped_at.replace(tzinfo=timezone.utc)
        else:
            scraped_at = scraped_at.astimezone(timezone.utc)
        warnings = []
        observed_fields = {"source_guid", "tracker_title", "freeleech_present"}

        description = None
        forum_url = None
        screenshots = None
        size_bytes = None
        size_source = None
        published_at = None
        category = None
        torrent_filename = None
        uploader = None
        views = None
        hits = None
        grabs = None
        file_count = None
        last_seed = None

        for label, value_cell in self._iter_label_value_rows(soup):
            value = value_cell.get_text(" ", strip=True)
            if label == "descarga":
                download_link = value_cell.find("a", href=re.compile(r"download\.php", re.IGNORECASE))
                if download_link:
                    torrent_filename = download_link.get_text(" ", strip=True) or None
                    observed_fields.add("torrent_filename")
            elif label.startswith("descripcion"):
                description = value
                observed_fields.add("description")
            elif label.startswith("capturas"):
                parsed_screenshots, screenshot_warnings = self._parse_screenshots(value_cell)
                warnings.extend(screenshot_warnings)
                if not screenshot_warnings:
                    screenshots = parsed_screenshots
                    observed_fields.add("screenshots")
            elif label.startswith("categoria"):
                category = value or None
                observed_fields.add("category")
            elif label.startswith("ultima semilla"):
                last_seed = value or None
                observed_fields.add("last_seed")
            elif label.startswith("tamano"):
                size_bytes, size_source = self._parse_size_field(value)
                observed_fields.add("size_bytes")
            elif label.startswith("anadido"):
                published_at = self._parse_spanish_datetime_utc(value)
                observed_fields.add("published_at")
            elif label == "vistas":
                views = self._parse_tracker_int(value)
                observed_fields.add("views")
            elif label == "hits":
                hits = self._parse_tracker_int(value)
                observed_fields.add("hits")
            elif label.startswith("descargado"):
                grabs = self._parse_tracker_int(value)
                observed_fields.add("grabs")
            elif label.startswith("uploader"):
                uploader = value or None
                observed_fields.add("uploader")
            elif label.startswith("num arch"):
                file_count = self._parse_tracker_int(value)
                observed_fields.add("file_count")

        technical = {}
        title_metadata = []
        tags_list = []
        technical_rows_seen = 0
        technical_complete = True
        for row in ficha_table.find_all("tr"):
            cols = row.find_all("td", recursive=False)
            if len(cols) != 2:
                continue
            normalized_key = self._normalize_label_text(cols[0].get_text(" ", strip=True)).rstrip(":")
            value = cols[1].get_text(separator=", ", strip=True)
            value_lower = value.lower()

            is_technical_row = any(
                marker in normalized_key
                for marker in ("video", "audio", "subtitulo", "contenedor", "caracteristica")
            )
            if is_technical_row:
                technical_rows_seen += 1
                if not value:
                    technical_complete = False
                    continue

            if "video" in normalized_key:
                technical["video"] = value
                title_metadata.append(f"[{value}]")
                if "4k" in value_lower or "2160" in value_lower: tags_list.append("4K")
                elif "1440" in value_lower or "2k" in value_lower: tags_list.append("1440p")
                elif "1080" in value_lower: tags_list.append("1080p")
                elif "720" in value_lower: tags_list.append("720p")
                elif "480" in value_lower: tags_list.append("480p")

                if "hevc" in value_lower or "x265" in value_lower or "h.265" in value_lower: tags_list.append("HEVC")
                elif "h.264" in value_lower or "x264" in value_lower or "avc" in value_lower: tags_list.append("H.264")
                elif "divx" in value_lower: tags_list.append("DivX")
                elif "mpeg2" in value_lower: tags_list.append("MPEG2")
                elif "wmv" in value_lower: tags_list.append("WMV")

                if "blu-ray4k" in value_lower or "bd4k" in value_lower: tags_list.append("Blu-ray4K")
                elif "blu-ray" in value_lower or "bd" in value_lower: tags_list.append("Blu-ray")
                elif "hd-dvd" in value_lower: tags_list.append("HD-DVD")
                elif "web" in value_lower: tags_list.append("Web")
                elif "hdtv" in value_lower: tags_list.append("HDTV")
                elif "dvd" in value_lower: tags_list.append("DVD")
                elif "dvb" in value_lower: tags_list.append("DVB")
                elif "laserdisc" in value_lower: tags_list.append("Laserdisc")
                elif "vhs" in value_lower: tags_list.append("VHS")
                elif "tv" in value_lower.split() or "tv," in value_lower: tags_list.append("TV")
            elif "audio" in normalized_key:
                technical["audio"] = value
                title_metadata.append(f"[Audio: {value}]")
                audio_tags = []
                audio_codecs = {"aac", "ac3", "flac", "mp3", "ogg", "alac", "wav", "dts"}
                for part in [item.strip() for item in value.split(",") if item.strip()]:
                    clean_part = part.replace("(", "").replace(")", "").strip()
                    if clean_part.lower() in audio_codecs and audio_tags:
                        audio_tags[-1] = f"{audio_tags[-1]} {clean_part.upper()}"
                    else:
                        audio_tags.append(clean_part.upper() if clean_part.lower() in audio_codecs else clean_part)
                tags_list.extend(f"Audio: {tag}" for tag in audio_tags)
            elif "subtitulo" in normalized_key:
                technical["subtitles"] = value
                normalized_value = value.replace("Castellano", "Espanol (Castellano)").replace("Latino", "Espanol (Latino)")
                title_metadata.append(f"[Subs: {normalized_value}]")
                tags_list.extend(f"Subs: {part.strip()}" for part in normalized_value.split(",") if part.strip())
            elif "contenedor" in normalized_key:
                technical["container"] = value
                title_metadata.append(f"[{value}]")
                if "mkv" in value_lower: tags_list.append("MKV")
                elif "mp4" in value_lower: tags_list.append("MP4")
                elif "avi" in value_lower: tags_list.append("AVI")
                elif "flv" in value_lower: tags_list.append("FLV")
                elif "ogm" in value_lower: tags_list.append("OGM")
                if "softsubs" in value_lower: tags_list.append("Softsubs")
                if "hardsubs" in value_lower: tags_list.append("Hardsubs")
                if "remux" in value_lower: tags_list.append("Remux")
            elif "caracteristica" in normalized_key:
                technical["features"] = value
                if "ordered chapters" in value_lower: tags_list.append("Ordered Chapters")
                if "softsubs" in value_lower: tags_list.append("Softsubs")
                if "hardsubs" in value_lower: tags_list.append("Hardsubs")
            elif "descarga directa" in normalized_key:
                forum_link = cols[1].find("a", href=re.compile(r"showthread\.php", re.IGNORECASE))
                forum_url = self._sanitize_remote_url(
                    forum_link.get("href") if forum_link else None,
                    allowed_hosts=frozenset({"foro.unionfansub.com"}),
                    allowed_query_keys=frozenset({"tid"}),
                )
                observed_fields.add("forum_url")

        if technical_rows_seen == 0:
            warnings.append("La ficha técnica no contenía campos reconocibles.")
        elif not technical_complete:
            warnings.append("La ficha técnica contenía campos vacíos y quedó incompleta.")
            technical = {}
            title_metadata = []
            tags_list = []
        else:
            observed_fields.add("tags")

        if forum_url is None:
            any_thread_link = soup.find("a", href=re.compile(r"showthread\.php", re.IGNORECASE))
            if any_thread_link:
                forum_url = self._sanitize_remote_url(
                    any_thread_link.get("href"),
                    allowed_hosts=frozenset({"foro.unionfansub.com"}),
                    allowed_query_keys=frozenset({"tid"}),
                )
                observed_fields.add("forum_url")

        tracker_seeders = None
        tracker_leechers = None
        peer_container = soup.find(id="cpeerlist")
        if peer_container:
            peer_text = peer_container.get_text(" ", strip=True).lower()
            seed_match = re.search(r"(\d[\d\.,]*)\s*semilla", peer_text, re.IGNORECASE)
            leech_match = re.search(r"(\d[\d\.,]*)\s*leecher", peer_text, re.IGNORECASE)
            if seed_match:
                tracker_seeders = self._parse_tracker_int(seed_match.group(1))
                observed_fields.add("tracker_seeders")
            if leech_match:
                tracker_leechers = self._parse_tracker_int(leech_match.group(1))
                observed_fields.add("tracker_leechers")

        freeleech_marker = soup.find(string=re.compile(r"FREEleech", re.IGNORECASE))
        freeleech_present = freeleech_marker is not None
        freeleech_until = None
        if freeleech_present:
            marker_text = str(freeleech_marker).strip()
            duration = re.search(
                r"FREEleech\s+durante\s*(?:(\d+)\s*d[ií]as?)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?",
                marker_text,
                re.IGNORECASE,
            )
            if duration:
                days = int(duration.group(1) or 0)
                hours = int(duration.group(2) or 0)
                minutes = int(duration.group(3) or 0)
                total_seconds = days * 86400 + hours * 3600 + minutes * 60
                if total_seconds > 0:
                    base_time = scraped_at
                    if base_time.tzinfo is not None:
                        base_time = base_time.astimezone(timezone.utc).replace(tzinfo=None)
                    freeleech_until = base_time + timedelta(seconds=total_seconds)
            if freeleech_until is None:
                warnings.append("Se observó freeleech, pero no se pudo calcular su vencimiento.")

        file_entries = None
        embedded_filelist = soup.find(id="cfilelist")
        if embedded_filelist:
            file_entries, embedded_was_truncated = self._parse_file_entries_container(embedded_filelist)
            if embedded_was_truncated:
                warnings.append("El filelist embebido supera 5000 archivos y quedó incompleto.")
                file_entries = None
            if file_entries is None and file_count == 0 and not embedded_was_truncated:
                file_entries = tuple()
            if (
                file_entries is not None
                and file_count is not None
                and len(file_entries) != file_count
            ):
                warnings.append(
                    "El filelist embebido quedó incompleto: "
                    f"se anunciaron {file_count} archivos y se obtuvieron {len(file_entries)}."
                )
                file_entries = None
            if file_entries is not None:
                observed_fields.add("file_entries")

        if size_bytes is None:
            warnings.append("La ficha no contiene un tamaño de torrent reconocible.")
        if published_at is None:
            warnings.append("La fecha de publicación no está presente o no se pudo interpretar.")

        formatted_fansub = self._extract_fansub_from_details(html_content)
        if formatted_fansub:
            observed_fields.add("fansub")

        tracker_metadata = {
            "version": TRACKER_METADATA_VERSION,
            "scrape_revision": self.SCRAPE_REVISION,
        }
        if technical_rows_seen > 0 and technical_complete:
            tracker_metadata["technical"] = technical
        present_metadata = {
            "category": category,
            "torrent_filename": torrent_filename,
            "uploader": uploader,
            "views": views,
            "hits": hits,
            "grabs": grabs,
            "file_count": file_count,
            "last_seed": last_seed,
        }
        tracker_metadata.update({
            key: value
            for key, value in present_metadata.items()
            if value is not None
        })
        if size_bytes is not None and size_source is not None:
            tracker_metadata["size"] = {"bytes": size_bytes, "source": size_source}
        if len(tracker_metadata) > 2:
            observed_fields.add("tracker_metadata")

        return TrackerSnapshot(
            source_guid=source_guid,
            tracker_title=tracker_title,
            scrape_revision=self.SCRAPE_REVISION,
            scraped_at=scraped_at,
            tracker_metadata=tracker_metadata,
            fansub=formatted_fansub,
            description=description,
            forum_url=forum_url,
            screenshots=screenshots,
            size_bytes=size_bytes,
            size_source=size_source,
            published_at=published_at,
            tracker_seeders=tracker_seeders,
            tracker_leechers=tracker_leechers,
            freeleech_present=freeleech_present,
            freeleech_until=freeleech_until,
            file_entries=file_entries,
            extra_info=" ".join(title_metadata),
            tags=tuple(dict.fromkeys(tags_list)),
            observed_fields=frozenset(observed_fields),
            partial=bool(warnings),
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------
    # Adapta HTML y GUID esperado opcional a la API dict histórica y devuelve
    # claves legacy más presencia/revisión/metadata tipadas. No hace red/DB ni
    # muta entradas, aunque el parser de fecha/zona puede escribir warnings y
    # usa el reloj UTC al no recibir observed_at. Propaga InvalidTrackerSnapshot
    # y errores de DOM/serialización. Depende de _parse_ficha_snapshot y de
    # TrackerSnapshot.to_payload para construir el resultado.
    # ------------------------------------------------------------
    def _parse_ficha_metadata(
        self,
        html_content: str,
        expected_source_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._parse_ficha_snapshot(html_content, expected_source_guid).to_payload()

    # ------------------------------------------------------------
    # Completa un TrackerSnapshot usando cookie y AsyncClient suministrados,
    # y devuelve una copia nueva con filelist/portada observados o warnings.
    # Hace red secuencial al tracker/foro, envía la cookie al host previsto y
    # no cierra ni muta el cliente del caller; no escribe DB. HTTPError del
    # filelist y ausencias opcionales se convierten en partial+warnings, pero
    # cancelación y errores inesperados de parser/DTO se propagan. Depende de
    # _parse_filelist_response, get_poster y dataclasses.replace, preservando
    # valores anteriores al no marcar colecciones incompletas como observadas.
    # ------------------------------------------------------------
    async def _complete_optional_snapshot_resources(
        self,
        snapshot: TrackerSnapshot,
        cookie_string: str,
        client: httpx.AsyncClient,
    ) -> TrackerSnapshot:
        warnings = list(snapshot.warnings)
        observed_fields = set(snapshot.observed_fields)
        file_entries = snapshot.file_entries
        poster_url = snapshot.poster_url

        expected_file_count = snapshot.tracker_metadata.get("file_count")
        if file_entries is None:
            filelist_url = (
                f"https://torrent.unionfansub.com/filelist.php?"
                f"id={snapshot.source_guid}&ajax=1"
            )
            headers = self._get_base_headers()
            headers["Cookie"] = cookie_string
            headers["Host"] = "torrent.unionfansub.com"
            try:
                response = await client.get(
                    filelist_url,
                    headers=headers,
                    timeout=30.0,
                    follow_redirects=False,
                )
                if response.status_code == 302:
                    warnings.append("El filelist redirigió a autenticación.")
                else:
                    response.raise_for_status()
                    parsed_entries, filelist_warning = self._parse_filelist_response(
                        response.text,
                        expected_file_count=expected_file_count,
                    )
                    if parsed_entries is not None:
                        file_entries = parsed_entries
                        observed_fields.add("file_entries")
                    if filelist_warning:
                        warnings.append(filelist_warning)
            except httpx.HTTPError as exc:
                warnings.append(f"No se pudo obtener el filelist: {type(exc).__name__}.")

        if snapshot.forum_url:
            poster_url = await self.get_poster(snapshot.forum_url, cookie_string, client=client)
            if poster_url is not None:
                observed_fields.add("poster_url")
            else:
                warnings.append("La ficha del foro no aportó una portada válida.")

        return replace(
            snapshot,
            poster_url=poster_url,
            file_entries=file_entries,
            observed_fields=frozenset(observed_fields),
            partial=bool(warnings),
            warnings=tuple(warnings),
        )

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
    # Comprueba una cookie mediante GET al explorador y valida también
    # el contrato HTML autenticado. Abre/cierra un cliente HTTP local,
    # devuelve False ante redirects, login servido con 200 o errores de
    # red y depende de _is_authenticated_tracker_page para la estructura.
    # ------------------------------------------------------------
    async def test_connection(self, cookie_string: str) -> bool:
        url = "https://torrent.unionfansub.com/browse.php"
        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "torrent.unionfansub.com"

        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                resp = await client.get(url, headers=headers, timeout=10.0)
                return resp.status_code == 200 and self._is_authenticated_tracker_page(resp.text)
        except (httpx.HTTPError, ValueError):
            return False

    # ------------------------------------------------------------
    # Extrae del HTML autenticado y devuelve dicts de descubrimiento con GUID,
    # título, fansub y URL details saneada; filas cortas, URLs ajenas e IDs no
    # numéricos se omiten. Construye un DOM local pero no hace red/DB, logging
    # ni muta la entrada. Errores inesperados de BeautifulSoup, urllib o helpers
    # se propagan. Depende de _extract_fansub_from_list_row,
    # _build_original_title, _sanitize_remote_url, urllib.parse y GUID_PREFIX;
    # tamaño/swarm se obtienen después desde el snapshot details.
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

            details_link = cols[1].find("a", href=re.compile(r"details\.php", re.IGNORECASE))
            details_url = self._sanitize_remote_url(
                details_link.get("href") if details_link else None,
                allowed_hosts=frozenset({"torrent.unionfansub.com"}),
                allowed_query_keys=frozenset({"id", "hit"}),
            )
            details_params = urllib.parse.parse_qs(urllib.parse.urlparse(details_url or "").query)
            torrent_id = str((details_params.get("id") or [""])[0])
            if not details_url or not torrent_id.isdigit():
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
    # Proyecta una fila TorrentCache ya existente al dict compatible de
    # búsqueda sin afirmar que hubo scraping remoto. Recibe la fila y
    # fallbacks de identidad/título/fansub, devuelve un payload nuevo y no
    # modifica DB ni hace I/O. Conserva métricas tracker separadas de qB y
    # omite el estado freeleech si nunca se observó; una fecha legacy implica
    # presencia verdadera. Solo depende del modelo TorrentCache y de
    # _format_datetime_to_rfc; una fila incompatible propaga su error al caller.
    # ------------------------------------------------------------
    def _build_cached_search_result(
        self,
        db_torrent: TorrentCache,
        source_guid: str,
        original_title: str,
        incoming_fansub: str,
    ) -> dict[str, Any]:
        cached_original_title = db_torrent.original_title or original_title
        published_at = getattr(db_torrent, "published_at", None)
        raw_size_bytes = getattr(db_torrent, "size_bytes", None)
        cached_size_bytes = (
            int(raw_size_bytes)
            if isinstance(raw_size_bytes, int) and not isinstance(raw_size_bytes, bool) and raw_size_bytes > 1
            else None
        )

        if hasattr(db_torrent, "tracker_seeders"):
            tracker_seeders = getattr(db_torrent, "tracker_seeders", None)
            tracker_leechers = getattr(db_torrent, "tracker_leechers", None)
        else:
            tracker_seeders = getattr(db_torrent, "peers_seeds", None)
            tracker_leechers = getattr(db_torrent, "peers_leechs", None)

        result: dict[str, Any] = {
            "guid": db_torrent.guid,
            "source_guid": db_torrent.source_guid or source_guid,
            "tracker_title": getattr(db_torrent, "tracker_title", None),
            "original_title": cached_original_title,
            "title": db_torrent.enriched_title or cached_original_title,
            "description": db_torrent.description,
            "poster_url": db_torrent.poster_url,
            "forum_url": getattr(db_torrent, "forum_url", None),
            "screenshots_json": getattr(db_torrent, "screenshots_json", None),
            "tracker_metadata_json": getattr(db_torrent, "tracker_metadata_json", None),
            "file_entries_json": getattr(db_torrent, "file_entries_json", None),
            "size_bytes": cached_size_bytes,
            "tracker_seeders": tracker_seeders,
            "tracker_leechers": tracker_leechers,
            "seeders": tracker_seeders,
            "leechers": tracker_leechers,
            "published_at": published_at,
            "publish_date": self._format_datetime_to_rfc(published_at),
            "last_scraped_at": getattr(db_torrent, "last_scraped_at", None),
            "scrape_revision": getattr(db_torrent, "scrape_revision", 0),
            "freeleech_until": db_torrent.freeleech_until,
            "raw_filenames": db_torrent.raw_filenames,
            "tags": db_torrent.tags,
            "fansub": db_torrent.fansub_name or incoming_fansub,
            "partial": False,
            "warnings": [],
            "scraped_from_tracker": False,
        }

        cached_freeleech = getattr(db_torrent, "tracker_freeleech", None)
        if cached_freeleech is None and db_torrent.freeleech_until is not None:
            cached_freeleech = True
        if cached_freeleech is not None:
            result["freeleech_present"] = bool(cached_freeleech)
            result["tracker_freeleech"] = bool(cached_freeleech)

        return result

    # ------------------------------------------------------------
    # Busca query con cookie y deadline opcional, y devuelve una lista de
    # payloads legacy desde caché o snapshots remotos; el corte puede producir
    # una lista parcial. Abre/cierra un AsyncClient, hace red, una lectura
    # SQLite corta, pausas de cortesía y logging, pero no persiste resultados.
    # Los fallos por ficha se registran y omiten; cualquier fallo global,
    # incluido auth/HTTP/DB/parser, se registra y se normaliza a []. Depende de
    # parsers de listado/details, control de deadline, Session/TorrentCache,
    # _build_cached_search_result y _complete_optional_snapshot_resources.
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

                    if not self._is_authenticated_tracker_page(resp.text):
                        raise InvalidTrackerSnapshot(
                            "El explorador no contiene la estructura autenticada esperada."
                        )

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
                        results.append(self._build_cached_search_result(
                            db_torrent,
                            source_guid,
                            original_title,
                            incoming_fansub,
                        ))
                    else:
                        deep_scraped += 1

                        try:
                            details_resp = await client.get(
                                details_url,
                                headers=headers,
                                timeout=30.0,
                                follow_redirects=False,
                            )
                            if details_resp.status_code == 302:
                                raise InvalidTrackerSnapshot(
                                    "El tracker redirigió a autenticación al abrir details.php."
                                )
                            details_resp.raise_for_status()

                            snapshot = self._parse_ficha_snapshot(
                                details_resp.text,
                                expected_source_guid=source_guid,
                            )
                            if snapshot.size_bytes is None:
                                raise InvalidTrackerSnapshot(
                                    "La ficha nueva no tiene un tamaño verificable y no se publicará."
                                )
                            snapshot = await self._complete_optional_snapshot_resources(
                                snapshot,
                                cookie_string,
                                client,
                            )
                            ficha_data = snapshot.to_payload()

                            formatted_fansub = (
                                snapshot.fansub
                                or incoming_fansub
                                or self._format_fansub_label("Union Fansub")
                            )
                            original_title = self._build_original_title(
                                formatted_fansub,
                                snapshot.tracker_title or base_title,
                            )
                            enriched_title = f"{original_title} {snapshot.extra_info}".strip()

                            ficha_data.update({
                                "guid": guid,
                                "source_guid": source_guid,
                                "original_title": original_title,
                                "title": enriched_title,
                                "fansub": formatted_fansub,
                            })
                            results.append(ficha_data)
                            await asyncio.sleep(0.5)
                        except (httpx.HTTPError, InvalidTrackerSnapshot, ValueError) as e:
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
    # Rehidrata source_guid con cookie, títulos fallback y AsyncClient opcional,
    # y devuelve payload snapshot compatible o None. Hace red y logging; usa el
    # cliente prestado sin cerrarlo o crea/cierra uno propio, nunca serializa la
    # cookie y no escribe DB ni avanza frescura. GUID/tamaño inválido, HTTP,
    # InvalidTrackerSnapshot y ValueError se normalizan a None (con log cuando
    # corresponde); cancelación y errores inesperados se propagan. Depende de
    # _parse_ficha_snapshot, _complete_optional_snapshot_resources, builders de
    # título y TrackerSnapshot.to_payload; opcionales no observados no se borran.
    # ------------------------------------------------------------
    async def rehydrate_torrent(
        self,
        source_guid: str,
        cookie_string: str,
        base_title: str,
        current_original_title: str = "",
        client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[Dict[str, Any]]:
        if not source_guid or not str(source_guid).isdigit():
            return None

        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "torrent.unionfansub.com"
        details_url = f"https://torrent.unionfansub.com/details.php?id={source_guid}&hit=1"
        owns_client = client is None
        active_client = client or httpx.AsyncClient(follow_redirects=False, timeout=30.0)

        try:
            details_resp = await active_client.get(
                details_url,
                headers=headers,
                timeout=30.0,
                follow_redirects=False,
            )
            if details_resp.status_code == 302:
                raise InvalidTrackerSnapshot("El tracker redirigió a autenticación durante rehydrate.")
            details_resp.raise_for_status()

            snapshot = self._parse_ficha_snapshot(
                details_resp.text,
                expected_source_guid=str(source_guid),
            )
            if snapshot.size_bytes is None:
                logger.warning(
                    f"[INDEXER] [{self.name}] Rehydrate rechazado sin tamaño "
                    f"para source_guid={source_guid}."
                )
                return None
            snapshot = await self._complete_optional_snapshot_resources(
                snapshot,
                cookie_string,
                active_client,
            )

            formatted_fansub = snapshot.fansub or self._format_fansub_label("Union Fansub")
            safe_base_title = (
                (snapshot.tracker_title or "").strip()
                or (base_title or "").strip()
                or (current_original_title or "").strip()
                or f"Torrent {source_guid}"
            )
            rebuilt_original_title = self._build_original_title(formatted_fansub, safe_base_title)
            enriched_title = f"{rebuilt_original_title} {snapshot.extra_info}".strip()

            payload = snapshot.to_payload()
            payload.update({
                "source_guid": str(source_guid),
                "fansub": formatted_fansub,
                "original_title": rebuilt_original_title,
            })
            if "tags" in snapshot.observed_fields:
                payload["title"] = enriched_title
            return payload
        except (httpx.HTTPError, InvalidTrackerSnapshot, ValueError) as e:
            logger.error(f"[INDEXER] [{self.name}] Error rehidratando ficha {source_guid}: {e}")
            return None
        finally:
            if owns_client:
                await active_client.aclose()

    # ------------------------------------------------------------
    # Obtiene desde url/cookie y AsyncClient opcional una URL de imagen
    # saneada o None. Hace red y logging, envía la cookie solo al host exacto
    # del foro y cierra únicamente el cliente creado aquí; no escribe DB ni
    # muta el cliente prestado. URL/login/redirect/portada inválidos y
    # HTTPError/ValueError se normalizan a None, mientras cancelación y errores
    # inesperados se propagan. Depende de _sanitize_remote_url,
    # _looks_like_login_page, selectores BeautifulSoup y httpx.AsyncClient.
    # ------------------------------------------------------------
    async def get_poster(
        self,
        url: str,
        cookie_string: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[str]:
        safe_forum_url = self._sanitize_remote_url(
            url,
            allowed_hosts=frozenset({"foro.unionfansub.com"}),
            allowed_query_keys=frozenset({"tid"}),
        )
        if safe_forum_url is None:
            logger.warning(f"[INDEXER] [{self.name}] Se rechazó una URL de foro no permitida.")
            return None

        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Host"] = "foro.unionfansub.com"
        owns_client = client is None
        active_client = client or httpx.AsyncClient(follow_redirects=False, timeout=15.0)

        try:
            response = await active_client.get(
                safe_forum_url,
                headers=headers,
                timeout=15.0,
                follow_redirects=False,
            )
            if 300 <= response.status_code < 400 and response.headers.get("location"):
                redirect_url = self._sanitize_remote_url(
                    response.headers.get("location"),
                    base_url=safe_forum_url,
                    allowed_hosts=frozenset({"foro.unionfansub.com"}),
                    allowed_query_keys=frozenset({"tid"}),
                )
                if redirect_url is None:
                    return None
                response = await active_client.get(
                    redirect_url,
                    headers=headers,
                    timeout=15.0,
                    follow_redirects=False,
                )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")
            if self._looks_like_login_page(soup):
                return None

            candidates = []
            portada_div = soup.find("div", class_="portada")
            if portada_div and portada_div.has_attr("style"):
                style_match = re.search(r"url\(['\"]?(.*?)['\"]?\)", portada_div["style"])
                if style_match:
                    candidates.append(style_match.group(1))

            portada_img = soup.select_one("div.portada img")
            if portada_img and portada_img.get("src"):
                candidates.append(portada_img.get("src"))

            og_img = soup.select_one('meta[property="og:image"]')
            if og_img and og_img.get("content"):
                candidates.append(og_img.get("content"))

            first_post_img = soup.select_one(".post_body img, .postcontent img, .post img")
            if first_post_img and first_post_img.get("src"):
                candidates.append(first_post_img.get("src"))

            for candidate in candidates:
                safe_image_url = self._sanitize_remote_url(
                    candidate,
                    base_url=safe_forum_url,
                    allowed_hosts=ALLOWED_TRACKER_HOSTS,
                    allowed_query_keys=frozenset({"id"}),
                )
                if safe_image_url:
                    return safe_image_url

            logger.warning(f"[INDEXER] [{self.name}] No se encontró portada válida en el hilo permitido.")
            return None
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"[INDEXER] [{self.name}] Error extrayendo portada: {type(e).__name__}.")
            return None
        finally:
            if owns_client:
                await active_client.aclose()

    # ------------------------------------------------------------
    # Solicita una URL de descarga y devuelve la Response final después de hasta
    # tres redirects HTTPS dentro de torrent.unionfansub.com. Recibe cliente,
    # URL y cabeceras, hace I/O remoto pero no toca DB ni registra credenciales.
    # Lanza TorrentDownloadError ante redirect ausente, externo o excesivo y
    # propaga errores HTTPX de transporte; depende de urllib.parse y AsyncClient.
    # ------------------------------------------------------------
    async def _get_torrent_response_with_safe_redirects(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
    ) -> httpx.Response:
        current_url = url
        for redirect_count in range(MAX_DOWNLOAD_REDIRECTS + 1):
            response = await client.get(
                current_url,
                headers=headers,
                timeout=15.0,
                follow_redirects=False,
            )
            if response.status_code not in DOWNLOAD_REDIRECT_STATUSES:
                return response

            location = response.headers.get("location")
            if not location:
                raise TorrentDownloadError("redirect_sin_destino")
            if redirect_count >= MAX_DOWNLOAD_REDIRECTS:
                raise TorrentDownloadError("demasiados_redirects")

            next_url = urllib.parse.urljoin(str(response.url), location)
            parsed = urllib.parse.urlparse(next_url)
            if (
                parsed.scheme.lower() != "https"
                or (parsed.hostname or "").lower() != "torrent.unionfansub.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
            ):
                raise TorrentDownloadError("redirect_fuera_del_tracker_permitido")
            current_url = next_url

        raise TorrentDownloadError("demasiados_redirects")

    # ------------------------------------------------------------
    # Valida bytes de metainfo y devuelve bool si forman un diccionario bencode
    # con una sección info también diccionario. No hace I/O, logging ni muta la
    # entrada; normaliza errores de decodificación a False. Depende de bencodepy
    # y la usan todos los candidatos de download_torrent antes de devolver bytes.
    # ------------------------------------------------------------
    def _is_valid_torrent_payload(self, payload: bytes) -> bool:
        if not payload:
            return False
        try:
            decoded = bencodepy.decode(payload)
        except Exception:
            return False
        return isinstance(decoded, dict) and isinstance(decoded.get(b"info"), dict)

    # ------------------------------------------------------------
    # Descarga el metainfo crudo de un GUID numérico probando las variantes
    # torrent= e id= de Union Fansub de forma independiente. Recibe GUID/cookie,
    # devuelve bytes bencode válidos, hace peticiones HTTPS y crea/cierra un
    # AsyncClient; no toca DB. Continúa tras HTTP/transporte/payload inválido y
    # termina con TorrentDownloadError sin secretos; depende de los dos helpers.
    # ------------------------------------------------------------
    async def download_torrent(self, guid: str, cookie_string: str) -> bytes:
        source_guid = str(guid or "").strip()
        if not source_guid.isdigit():
            raise TorrentDownloadError("source_guid_no_numerico")

        headers = self._get_base_headers()
        headers["Cookie"] = cookie_string
        headers["Referer"] = f"https://torrent.unionfansub.com/details.php?id={source_guid}"
        headers["Accept"] = "application/x-bittorrent,application/octet-stream;q=0.9,*/*;q=0.8"
        headers["Sec-Fetch-Site"] = "same-origin"

        candidates = (
            ("torrent", f"https://torrent.unionfansub.com/download.php?torrent={source_guid}"),
            ("id", f"https://torrent.unionfansub.com/download.php?id={source_guid}"),
        )
        diagnostics = []
        async with httpx.AsyncClient(follow_redirects=False) as client:
            for candidate_name, candidate_url in candidates:
                try:
                    response = await self._get_torrent_response_with_safe_redirects(
                        client,
                        candidate_url,
                        headers,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    diagnostics.append(f"{candidate_name}:http_{exc.response.status_code}")
                    continue
                except httpx.RequestError as exc:
                    diagnostics.append(f"{candidate_name}:{type(exc).__name__}")
                    continue
                except TorrentDownloadError as exc:
                    diagnostics.append(f"{candidate_name}:{exc}")
                    continue

                if self._is_valid_torrent_payload(response.content):
                    return response.content

                content_type = response.headers.get("content-type", "").lower()
                payload_kind = "html_o_login" if "html" in content_type else "metainfo_invalido"
                diagnostics.append(f"{candidate_name}:{payload_kind}")

        summary = ",".join(diagnostics) or "sin_respuesta"
        raise TorrentDownloadError(f"fallaron_variantes[{summary}]")
