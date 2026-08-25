import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Any, Mapping, Optional


class InvalidTrackerSnapshot(ValueError):
    """Indica que una respuesta remota no cumple el contrato mínimo de una ficha."""


# ------------------------------------------------------------
# Mezcla recursivamente los mappings current e incoming y devuelve un dict
# nuevo: conserva claves ausentes/None y aplica cero, false, strings y listas
# vacías. No modifica las entradas ni hace I/O o logging. Puede propagar
# TypeError/ValueError al copiar mappings o convertir claves, y RecursionError
# ante objetos circulares/profundamente anidados. Depende de Mapping, dict y
# de sí misma para objetos hijos; merge_tracker_metadata_json es su caller.
# ------------------------------------------------------------
def _merge_present_metadata(
    current: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    for raw_key, incoming_value in incoming.items():
        key = str(raw_key)
        if incoming_value is None:
            continue
        current_value = merged.get(key)
        if isinstance(incoming_value, Mapping):
            current_child = current_value if isinstance(current_value, Mapping) else {}
            merged[key] = _merge_present_metadata(current_child, incoming_value)
        else:
            merged[key] = incoming_value
    return merged


# ------------------------------------------------------------
# Fusiona metadata tracker parcial: recibe el JSON persistido opcional y
# un mapping entrante opcional, y devuelve JSON canónico o None si el
# resultado está vacío. No modifica entradas ni hace I/O; preserva claves
# ausentes/None y aplica cero, false y listas vacías. Descarta sin propagar
# JSON previo inválido/no-objeto, pero json.dumps puede lanzar TypeError,
# ValueError o RecursionError si el entrante no es JSON-compatible o es
# circular/profundamente anidado. Depende de json y de
# _merge_present_metadata para la fusión recursiva.
# ------------------------------------------------------------
def merge_tracker_metadata_json(
    current_json: Optional[str],
    incoming_metadata: Optional[Mapping[str, Any]],
) -> Optional[str]:
    current: dict[str, Any] = {}
    if current_json:
        try:
            decoded = json.loads(current_json)
            if isinstance(decoded, dict):
                current = decoded
        except (TypeError, ValueError):
            current = {}

    merged = _merge_present_metadata(current, incoming_metadata or {})
    if not merged:
        return None
    return json.dumps(
        merged,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class TrackerScreenshot:
    thumbnail_url: str
    gallery_url: Optional[str] = None
    alt: Optional[str] = None

    # ------------------------------------------------------------
    # Valida al construir el DTO sus entradas thumbnail_url, gallery_url
    # y alt, y devuelve implícitamente None. No modifica campos, hace I/O
    # ni logging. Lanza InvalidTrackerSnapshot si falta la miniatura o si
    # una URL/texto supera el contrato; la dataclass invoca este método
    # automáticamente tras __init__ y depende de InvalidTrackerSnapshot.
    # ------------------------------------------------------------
    def __post_init__(self) -> None:
        if not self.thumbnail_url or len(self.thumbnail_url) > 2048:
            raise InvalidTrackerSnapshot("La miniatura de una captura no es válida.")
        if self.gallery_url is not None and len(self.gallery_url) > 2048:
            raise InvalidTrackerSnapshot("La URL de galería de una captura es demasiado larga.")
        if self.alt is not None and len(self.alt) > 300:
            raise InvalidTrackerSnapshot("El texto alternativo de una captura es demasiado largo.")

    # ------------------------------------------------------------
    # Convierte los campos validados de esta captura, sin entradas adicionales,
    # y devuelve un dict apto para la serialización posterior. No modifica
    # estado ni hace I/O o logging. No captura errores: bajo el constructor
    # validado solo accede a atributos, y cualquier AttributeError de una
    # instancia corrupta se propaga. TrackerSnapshot.to_payload consume la
    # salida antes de llamar a json.dumps.
    # ------------------------------------------------------------
    def to_dict(self) -> dict[str, Optional[str]]:
        return {
            "thumbnail_url": self.thumbnail_url,
            "gallery_url": self.gallery_url,
            "alt": self.alt,
        }


@dataclass(frozen=True)
class TrackerFileEntry:
    path: str
    size_bytes: Optional[int] = None

    # ------------------------------------------------------------
    # Valida al construir el DTO las entradas path y size_bytes, distingue
    # tamaño ausente de cero real y devuelve implícitamente None. No muta
    # campos ni hace I/O o logging. Lanza InvalidTrackerSnapshot ante una
    # ruta vacía/demasiado larga o un tamaño negativo; la dataclass lo
    # invoca tras __init__ y depende de InvalidTrackerSnapshot.
    # ------------------------------------------------------------
    def __post_init__(self) -> None:
        if not self.path or len(self.path) > 4096:
            raise InvalidTrackerSnapshot("La ruta de un archivo no es válida.")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise InvalidTrackerSnapshot("El tamaño de un archivo no puede ser negativo.")

    # ------------------------------------------------------------
    # Convierte path y size_bytes de esta entrada, sin argumentos adicionales,
    # y devuelve un dict conservando None como tamaño desconocido. No altera
    # estado ni hace I/O o logging. No captura errores: con el DTO validado el
    # acceso es directo, y cualquier AttributeError de una instancia corrupta
    # se propaga. TrackerSnapshot.to_payload consume la salida para JSON.
    # ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class TrackerSnapshot:
    source_guid: str
    tracker_title: str
    scrape_revision: int
    scraped_at: datetime
    tracker_metadata: Mapping[str, Any]
    fansub: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    forum_url: Optional[str] = None
    screenshots: Optional[tuple[TrackerScreenshot, ...]] = None
    size_bytes: Optional[int] = None
    size_source: Optional[str] = None
    published_at: Optional[datetime] = None
    tracker_seeders: Optional[int] = None
    tracker_leechers: Optional[int] = None
    freeleech_present: bool = False
    freeleech_until: Optional[datetime] = None
    file_entries: Optional[tuple[TrackerFileEntry, ...]] = None
    extra_info: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    observed_fields: frozenset[str] = field(default_factory=frozenset)
    partial: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------
    # Valida al construir el DTO todos sus campos de identidad, revisión,
    # presencia, unidades, timestamps, contadores y límites, y devuelve
    # implícitamente None. No muta el snapshot ni hace I/O o logging;
    # preserva None como no observado. Lanza InvalidTrackerSnapshot ante
    # cualquier invariante incumplido. La dataclass lo invoca tras __init__
    # y depende de los TrackerScreenshot/TrackerFileEntry ya construidos y
    # de la versión declarada en tracker_metadata.
    # ------------------------------------------------------------
    def __post_init__(self) -> None:
        if not self.source_guid or not self.source_guid.isdigit():
            raise InvalidTrackerSnapshot("La ficha no contiene un identificador numérico válido.")
        if not self.tracker_title or len(self.tracker_title) > 1000:
            raise InvalidTrackerSnapshot("La ficha no contiene un título válido.")
        if self.scrape_revision <= 0:
            raise InvalidTrackerSnapshot("La revisión del scraper debe ser positiva.")
        if self.size_bytes is not None and self.size_bytes <= 1:
            raise InvalidTrackerSnapshot("El tamaño observado del torrent no puede ser el centinela 0/1.")
        if (self.size_bytes is None) != (self.size_source is None):
            raise InvalidTrackerSnapshot("El tamaño y su origen deben estar presentes conjuntamente.")
        if self.tracker_seeders is not None and self.tracker_seeders < 0:
            raise InvalidTrackerSnapshot("El número de semillas no puede ser negativo.")
        if self.tracker_leechers is not None and self.tracker_leechers < 0:
            raise InvalidTrackerSnapshot("El número de leechers no puede ser negativo.")
        if self.published_at is not None:
            if self.published_at.tzinfo is None or self.published_at.utcoffset() != timedelta(0):
                raise InvalidTrackerSnapshot("published_at debe ser un datetime UTC con zona horaria.")
        if self.scraped_at.tzinfo is None or self.scraped_at.utcoffset() != timedelta(0):
            raise InvalidTrackerSnapshot("scraped_at debe ser un datetime UTC con zona horaria.")
        if not self.freeleech_present and self.freeleech_until is not None:
            raise InvalidTrackerSnapshot("No puede existir vencimiento sin presencia de freeleech.")
        if len(self.screenshots or ()) > 24:
            raise InvalidTrackerSnapshot("La ficha supera el límite de capturas permitido.")
        if len(self.file_entries or ()) > 5000:
            raise InvalidTrackerSnapshot("La ficha supera el límite de archivos permitido.")
        metadata_version = self.tracker_metadata.get("version")
        if not isinstance(metadata_version, int) or metadata_version <= 0:
            raise InvalidTrackerSnapshot("tracker_metadata debe declarar una versión positiva.")

    # ------------------------------------------------------------
    # Serializa este snapshot, sin entradas adicionales, y devuelve el
    # dict compatible con callers legacy más campos tipados/JSON. No muta
    # estado, hace I/O ni logging, y nunca incluye cookies o URLs autenticadas;
    # observed_fields gobierna colecciones completas y vacíos reales. Puede
    # propagar TypeError/ValueError de json o de fechas/metadata no
    # serializables. Depende de TrackerScreenshot.to_dict,
    # TrackerFileEntry.to_dict, json.dumps y format_datetime para los puentes.
    # ------------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        screenshots = None
        if self.screenshots is not None and "screenshots" in self.observed_fields:
            screenshots = [item.to_dict() for item in self.screenshots]

        file_entries = None
        raw_filenames = None
        if self.file_entries is not None and "file_entries" in self.observed_fields:
            file_entries = [item.to_dict() for item in self.file_entries]
            raw_filenames = [item.path for item in self.file_entries]

        publish_date = None
        if self.published_at is not None:
            publish_date = format_datetime(self.published_at.astimezone(timezone.utc), usegmt=True)

        tracker_metadata = None
        if "tracker_metadata" in self.observed_fields:
            tracker_metadata = dict(self.tracker_metadata)

        return {
            "source_guid": self.source_guid,
            "tracker_title": self.tracker_title,
            "fansub": self.fansub,
            "description": self.description,
            "poster_url": self.poster_url,
            "forum_url": self.forum_url,
            "screenshots": screenshots,
            "screenshots_json": json.dumps(screenshots, ensure_ascii=False) if screenshots is not None else None,
            "size_bytes": self.size_bytes,
            "size_source": self.size_source,
            "published_at": self.published_at,
            "publish_date": publish_date,
            "last_scraped_at": self.scraped_at,
            "tracker_seeders": self.tracker_seeders,
            "tracker_leechers": self.tracker_leechers,
            "seeders": self.tracker_seeders,
            "leechers": self.tracker_leechers,
            "freeleech_present": self.freeleech_present,
            "tracker_freeleech": self.freeleech_present,
            "freeleech_until": self.freeleech_until,
            "file_entries": file_entries,
            "file_entries_json": json.dumps(file_entries, ensure_ascii=False) if file_entries is not None else None,
            "raw_filenames": json.dumps(raw_filenames, ensure_ascii=False) if raw_filenames is not None else None,
            "extra_info": self.extra_info,
            "tags": (
                json.dumps(list(self.tags), ensure_ascii=False)
                if self.tags or "tags" in self.observed_fields
                else None
            ),
            "tracker_metadata": tracker_metadata,
            "tracker_metadata_json": (
                json.dumps(tracker_metadata, ensure_ascii=False)
                if tracker_metadata is not None
                else None
            ),
            "scrape_revision": self.scrape_revision,
            "observed_fields": sorted(self.observed_fields),
            "partial": self.partial,
            "warnings": list(self.warnings),
            "structurally_valid": True,
            "scraped_from_tracker": True,
        }
