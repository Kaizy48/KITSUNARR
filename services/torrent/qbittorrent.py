import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Mapping

import httpx

from core.app.logger import logger


class QBittorrentError(RuntimeError):
    """Error base seguro de lectura y alta explícita con qBittorrent."""


class QBittorrentAuthenticationError(QBittorrentError):
    """La WebAPI rechazó o no pudo crear una sesión autenticada."""


class QBittorrentResponseError(QBittorrentError):
    """La WebAPI respondió con HTTP o JSON que incumple el contrato esperado."""


# ------------------------------------------------------------
# Representa la única sesión WebAPI cacheada por proceso. La clave contiene URL,
# usuario y huella SHA-256 de la contraseña, nunca el secreto en claro; cookies es
# una tupla inmutable para impedir mutaciones accidentales entre pollers.
# ------------------------------------------------------------
@dataclass(frozen=True)
class _CachedSession:
    key: tuple[str, str, str]
    cookies: tuple[tuple[str, str], ...]


_cached_session: _CachedSession | None = None
_session_lock = asyncio.Lock()
_INFO_HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


# ------------------------------------------------------------
# Recibe una URL configurada y devuelve la base sin slash final. No hace red ni
# muta configuración; rechaza valores sin esquema/host HTTP(S) mediante
# QBittorrentResponseError. Depende del parser URL de HTTPX.
# ------------------------------------------------------------
def _normalize_base_url(url: str) -> str:
    normalized = (url or "").strip().rstrip("/")
    try:
        parsed = httpx.URL(normalized)
    except Exception as exc:
        raise QBittorrentResponseError("La URL de qBittorrent no es válida.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise QBittorrentResponseError("La URL de qBittorrent debe usar HTTP o HTTPS.")
    return normalized


# ------------------------------------------------------------
# Recibe URL normalizada, usuario y contraseña y devuelve una clave de caché O(1).
# No hace I/O ni conserva el secreto: solo su SHA-256 para detectar cambios de
# credenciales. Depende de hashlib y no propaga errores con entradas string.
# ------------------------------------------------------------
def _build_cache_key(base_url: str, username: str, password: str) -> tuple[str, str, str]:
    password_fingerprint = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    return base_url, username or "", password_fingerprint


# ------------------------------------------------------------
# Extrae del Set-Cookie las sesiones documentadas por qBittorrent: SID en v4/v5.1
# y QBT_SID_<puerto> desde v5.2. Devuelve un dict nuevo, no registra valores y
# descarta cookies ajenas; una respuesta sin cookie representa whitelist local.
# ------------------------------------------------------------
def _extract_session_cookies(cookies: httpx.Cookies) -> dict[str, str]:
    return {
        name: value
        for name, value in cookies.items()
        if name == "SID" or name.startswith("QBT_SID_")
    }


# ------------------------------------------------------------
# Recibe una clave concreta o None y elimina la sesión cacheada si coincide; con
# None vacía siempre. Devuelve None, muta solo el slot global bajo lock y se usa al
# recibir 403 o cambiar configuración. No hace red y propaga fallos del lock.
# ------------------------------------------------------------
async def invalidate_qbittorrent_session(cache_key: tuple[str, str, str] | None = None) -> None:
    global _cached_session
    async with _session_lock:
        if cache_key is None or (_cached_session and _cached_session.key == cache_key):
            _cached_session = None


# ------------------------------------------------------------
# Inicia o reutiliza una sesión WebAPI. Recibe URL/usuario/password y force_refresh;
# devuelve todas las cookies de sesión compatibles, {} si una whitelist autoriza
# sin cookie o None ante login/red inválidos. Hace POST form a auth/login, envía
# Referer conforme a la WebAPI, conserva un único slot O(1) sin contraseña y
# reconoce 200 Ok. legacy y 204 de qBittorrent 5.2. No expone cookies/secretos en log.
# ------------------------------------------------------------
async def qbittorrent_login(
    url: str,
    username: str,
    password: str,
    *,
    force_refresh: bool = False,
) -> dict[str, str] | None:
    global _cached_session

    try:
        base_url = _normalize_base_url(url)
    except QBittorrentResponseError as exc:
        logger.error(f"❌ qBittorrent Login fallido: {exc}")
        return None
    cache_key = _build_cache_key(base_url, username, password)

    async with _session_lock:
        if not force_refresh and _cached_session and _cached_session.key == cache_key:
            return dict(_cached_session.cookies)

        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"Referer": base_url}) as client:
                response = await client.post(
                    f"{base_url}/api/v2/auth/login",
                    data={"username": username, "password": password},
                )
        except httpx.HTTPError as exc:
            logger.error(f"❌ qBittorrent Error de conexión durante login: {type(exc).__name__}")
            return None

        body = response.text.strip()
        login_ok = response.status_code == 204 or (
            response.status_code == 200 and body == "Ok."
        )
        if login_ok:
            session_cookies = _extract_session_cookies(response.cookies)
            _cached_session = _CachedSession(cache_key, tuple(session_cookies.items()))
            return dict(session_cookies)

        if response.status_code == 403:
            logger.error("❌ qBittorrent Login fallido: IP bloqueada o acceso prohibido.")
        elif response.status_code in {401, 200} and body in {"Fails.", "Ban."}:
            logger.error("❌ qBittorrent Login fallido: credenciales rechazadas.")
        else:
            logger.error(
                f"❌ qBittorrent Login fallido: respuesta inesperada HTTP {response.status_code}."
            )
        return None


# ------------------------------------------------------------
# Ejecuta un GET WebAPI autenticado y devuelve Response. Recibe credenciales,
# ruta y params; reutiliza la sesión O(1), renueva una vez ante 403 y usa un cliente
# HTTP corto. Eleva QBittorrentAuthenticationError para auth y
# QBittorrentResponseError para transporte/HTTP; no registra cookies/password.
# ------------------------------------------------------------
async def _authenticated_get(
    url: str,
    username: str,
    password: str,
    path: str,
    *,
    params: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> httpx.Response:
    base_url = _normalize_base_url(url)
    cache_key = _build_cache_key(base_url, username, password)
    cookies = await qbittorrent_login(base_url, username, password)
    if cookies is None:
        raise QBittorrentAuthenticationError("No se pudo iniciar sesión en qBittorrent.")

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                timeout=timeout,
                headers={"Referer": base_url},
            ) as client:
                response = await client.get(f"{base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise QBittorrentResponseError(
                f"No se pudo comunicar con qBittorrent ({type(exc).__name__})."
            ) from exc

        if response.status_code != 403:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise QBittorrentResponseError(
                    f"qBittorrent respondió HTTP {response.status_code}."
                ) from exc
            return response

        await invalidate_qbittorrent_session(cache_key)
        if attempt == 0:
            cookies = await qbittorrent_login(base_url, username, password, force_refresh=True)
            if cookies is not None:
                continue
        raise QBittorrentAuthenticationError("La sesión de qBittorrent fue rechazada.")

    raise QBittorrentAuthenticationError("No se pudo renovar la sesión de qBittorrent.")


# ------------------------------------------------------------
# Ejecuta un POST WebAPI autenticado. Recibe credenciales, ruta, formulario y/o
# multipart; devuelve Response, reutiliza la sesión O(1) y renueva una vez ante
# 403. Hace red con un cliente corto y eleva errores qB tipados sin registrar
# cookies, password ni metainfo. Depende del mismo lifecycle que _authenticated_get.
# ------------------------------------------------------------
async def _authenticated_post(
    url: str,
    username: str,
    password: str,
    path: str,
    *,
    data: Mapping[str, str] | None = None,
    files: dict | None = None,
    timeout: float = 15.0,
) -> httpx.Response:
    base_url = _normalize_base_url(url)
    cache_key = _build_cache_key(base_url, username, password)
    cookies = await qbittorrent_login(base_url, username, password)
    if cookies is None:
        raise QBittorrentAuthenticationError("No se pudo iniciar sesión en qBittorrent.")

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                timeout=timeout,
                headers={"Referer": base_url},
            ) as client:
                response = await client.post(
                    f"{base_url}{path}",
                    data=data,
                    files=files,
                )
        except httpx.HTTPError as exc:
            raise QBittorrentResponseError(
                f"No se pudo comunicar con qBittorrent ({type(exc).__name__})."
            ) from exc

        if response.status_code != 403:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise QBittorrentResponseError(
                    f"qBittorrent respondió HTTP {response.status_code}."
                ) from exc
            return response

        await invalidate_qbittorrent_session(cache_key)
        if attempt == 0:
            cookies = await qbittorrent_login(base_url, username, password, force_refresh=True)
            if cookies is not None:
                continue
        raise QBittorrentAuthenticationError("La sesión de qBittorrent fue rechazada.")

    raise QBittorrentAuthenticationError("No se pudo renovar la sesión de qBittorrent.")


# ------------------------------------------------------------
# Añade al cliente un metainfo original en ejecución inmediata. Recibe conexión,
# bytes y nombre seguro, devuelve None tras un POST multipart torrents/add; muta
# qBittorrent pero no DB ni archivos locales. qB decide ruta/categoría según su
# configuración y los errores 415/auth/red se propagan tipados. Depende de la
# WebAPI v2 y _authenticated_post.
# ------------------------------------------------------------
async def add_torrent(
    url: str,
    username: str,
    password: str,
    torrent_bytes: bytes,
    filename: str,
) -> None:
    safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "kitsunarr.torrent")
    if not safe_filename.lower().endswith(".torrent"):
        safe_filename = f"{safe_filename}.torrent"
    await _authenticated_post(
        url,
        username,
        password,
        "/api/v2/torrents/add",
        files={
            "torrents": (
                safe_filename[:180],
                torrent_bytes,
                "application/x-bittorrent",
            )
        },
        timeout=20.0,
    )


# ------------------------------------------------------------
# Detiene un torrent concreto durante verificaciones operativas. Recibe conexión
# e info hash, devuelve None tras POST torrents/stop y muta solo ese torrent en
# qBittorrent; valida 40/64 hex y propaga errores tipados. No se expone como acción
# WebUI y depende del contrato start/stop de qBittorrent 5.x.
# ------------------------------------------------------------
async def stop_torrent(
    url: str,
    username: str,
    password: str,
    info_hash: str,
) -> None:
    normalized_hash = (info_hash or "").strip().lower()
    if not _INFO_HASH_RE.fullmatch(normalized_hash):
        raise QBittorrentResponseError("El info hash no tiene un formato válido.")
    await _authenticated_post(
        url,
        username,
        password,
        "/api/v2/torrents/stop",
        data={"hashes": normalized_hash},
    )


# ------------------------------------------------------------
# Obtiene telemetría por info hash. Recibe URL/credenciales/hash y devuelve el DTO
# de estado o None solo cuando qBittorrent confirma que no existe; hace GET con
# params codificados, valida hash 40/64 hex, JSON lista y coincidencia exacta.
# Propaga errores qB tipados de login/red/HTTP/protocolo y no confunde fallo con
# not_found. Depende de _authenticated_get y del contrato torrents/info v2.
# ------------------------------------------------------------
async def get_torrent_telemetry(
    url: str,
    username: str,
    password: str,
    info_hash: str,
) -> dict | None:
    normalized_hash = (info_hash or "").strip().lower()
    if not _INFO_HASH_RE.fullmatch(normalized_hash):
        raise QBittorrentResponseError("El info hash no tiene un formato válido.")

    response = await _authenticated_get(
        url,
        username,
        password,
        "/api/v2/torrents/info",
        params={"hashes": normalized_hash},
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise QBittorrentResponseError("qBittorrent devolvió JSON inválido.") from exc
    if not isinstance(payload, list):
        raise QBittorrentResponseError("qBittorrent devolvió una lista de torrents inválida.")

    torrent = next(
        (
            item
            for item in payload
            if isinstance(item, dict)
            and str(item.get("hash") or "").strip().lower() == normalized_hash
        ),
        None,
    )
    if torrent is None:
        return None
    return {
        "progress": torrent.get("progress", 0.0),
        "client_status": torrent.get("state", "unknown"),
        "download_speed": torrent.get("dlspeed", 0),
        "upload_speed": torrent.get("upspeed", 0),
        "eta": torrent.get("eta", 0),
        "peers_seeds": torrent.get("num_seeds", 0),
        "peers_leechs": torrent.get("num_leechs", 0),
        "ratio": torrent.get("ratio", 0.0),
        "name": torrent.get("name"),
        "size": torrent.get("size", 0),
    }


# ------------------------------------------------------------
# Lista todos los torrents visibles para el laboratorio manual. Recibe URL y
# credenciales y devuelve DTOs con hash/nombre/tamaño/estado; hace un GET autenticado
# con renovación, valida JSON lista y omite filas sin hash válido. Propaga errores
# qB tipados en vez de convertirlos en lista vacía. No filtra por tracker pese al
# nombre legacy de la función y depende de _authenticated_get.
# ------------------------------------------------------------
async def get_all_unionfansub_torrents(url: str, username: str, password: str) -> list[dict]:
    response = await _authenticated_get(
        url,
        username,
        password,
        "/api/v2/torrents/info",
        timeout=15.0,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise QBittorrentResponseError("qBittorrent devolvió JSON inválido.") from exc
    if not isinstance(payload, list):
        raise QBittorrentResponseError("qBittorrent devolvió una lista de torrents inválida.")

    return [
        {
            "info_hash": torrent.get("hash"),
            "name": torrent.get("name"),
            "size": torrent.get("size", 0),
            "progress": torrent.get("progress", 0.0),
            "state": torrent.get("state", "unknown"),
            "tracker": torrent.get("tracker", ""),
            "tags": torrent.get("tags", ""),
            "category": torrent.get("category", ""),
        }
        for torrent in payload
        if isinstance(torrent, dict)
        and _INFO_HASH_RE.fullmatch(str(torrent.get("hash") or "").strip().lower())
    ]
