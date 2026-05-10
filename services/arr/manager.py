import asyncio
import httpx
from core.app.logger import logger

# ------------------------------------------------------------
# Normaliza la URL de Sonarr o Radarr para que Kitsunarr pueda
# llamar siempre a la API v3 desde una base consistente.
# ------------------------------------------------------------
def _normalize_arr_url(app_url: str) -> str:
    clean_app_url = app_url.rstrip("/")
    if clean_app_url.endswith("/api/v3"):
        return clean_app_url[:-7]
    if clean_app_url.endswith("/api"):
        return clean_app_url[:-4]
    return clean_app_url


# ------------------------------------------------------------
# Identifica si un indexador existente en Sonarr/Radarr corresponde
# a Kitsunarr por nombre o por URL base Torznab.
# ------------------------------------------------------------
def _is_kitsunarr_indexer(idx: dict, clean_kitsunarr_url: str) -> bool:
    return (
        str(idx.get("name", "")).strip().lower() == "kitsunarr"
        or (
            idx.get("implementation") == "Torznab"
            and any(
                f.get("name") == "baseUrl" and str(f.get("value", "")).rstrip("/") == clean_kitsunarr_url
                for f in idx.get("fields", [])
            )
        )
    )


# ------------------------------------------------------------
# Busca en Sonarr/Radarr si Kitsunarr ya está registrado como
# indexador Torznab.
# ------------------------------------------------------------
async def _find_existing_kitsunarr_indexer(clean_app_url: str, headers: dict, clean_kitsunarr_url: str) -> dict | None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
        resp = await client.get(f"{clean_app_url}/api/v3/indexer", headers=headers)
        resp.raise_for_status()
        existing_indexers = resp.json()
        return next((idx for idx in existing_indexers if _is_kitsunarr_indexer(idx, clean_kitsunarr_url)), None)


# ------------------------------------------------------------
# Recomprueba una sincronización ambigua para detectar casos donde
# Arr guardó el indexador aunque devolviera error o timeout.
# ------------------------------------------------------------
async def _confirm_existing_after_ambiguous_sync(clean_app_url: str, headers: dict, clean_kitsunarr_url: str) -> bool:
    await asyncio.sleep(2.0)
    existing_indexer = await _find_existing_kitsunarr_indexer(clean_app_url, headers, clean_kitsunarr_url)
    return bool(existing_indexer)

# ------------------------------------------------------------
# Sincroniza Kitsunarr en Sonarr o Radarr como indexador Torznab.
# Crea o actualiza la entrada, configura categorías y devuelve a la
# UI un resultado claro incluso si Arr responde de forma ambigua.
# ------------------------------------------------------------
async def sync_indexer_to_arr(app_type: str, app_url: str, app_api_key: str, kitsunarr_url: str, kitsunarr_api_key: str) -> dict:
    clean_app_url = _normalize_arr_url(app_url)

    clean_kitsunarr_url = kitsunarr_url.rstrip("/")
    
    headers = {
        "X-Api-Key": app_api_key,
        "Content-Type": "application/json"
    }
    
    fields = [
        {"name": "baseUrl", "value": clean_kitsunarr_url},
        {"name": "apiPath", "value": "/api"},
        {"name": "apiKey", "value": kitsunarr_api_key}
    ]
    
    if app_type == "sonarr":
        fields.extend([
            {"name": "categories", "value": [5000, 5030, 5040, 5070]},
            {"name": "animeCategories", "value": [5070]},
            {"name": "animeStandardFormatSearch", "value": False},
        ])
    else:
        fields.extend([
            {"name": "categories", "value": [2000, 2030, 2040, 2045]}
        ])
    
    payload = {
        "enableRss": True,
        "enableAutomaticSearch": True,
        "enableInteractiveSearch": True,
        "supportsRss": True,
        "supportsSearch": True,
        "protocol": "torrent",
        "priority": 25,
        "name": "KITSUNARR",
        "fields": fields,
        "implementationName": "Torznab",
        "implementation": "Torznab",
        "configContract": "TorznabSettings"
    }

    sync_keys = [
        "enableRss",
        "enableAutomaticSearch",
        "enableInteractiveSearch",
        "supportsRss",
        "supportsSearch",
        "protocol",
        "priority",
        "name",
        "implementationName",
        "implementation",
        "configContract",
    ]

    try:
        arr_timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=arr_timeout) as client:
            resp = await client.get(f"{clean_app_url}/api/v3/indexer", headers=headers)
            if resp.status_code == 401:
                return {"success": False, "error": "API Key de Arr denegada (401)"}
            resp.raise_for_status()
            
            existing_indexers = resp.json()
            existing_indexer = next((idx for idx in existing_indexers if _is_kitsunarr_indexer(idx, clean_kitsunarr_url)), None)
            
            if existing_indexer:
                existing_fields = {f.get("name"): f for f in existing_indexer.get("fields", []) if f.get("name")}
                for field in fields:
                    existing_fields[field["name"]] = {"name": field["name"], "value": field["value"]}

                for key in sync_keys:
                    existing_indexer[key] = payload[key]
                existing_indexer["fields"] = list(existing_fields.values())
                
                put_resp = await client.put(f"{clean_app_url}/api/v3/indexer/{existing_indexer['id']}", json=existing_indexer, headers=headers)
                put_resp.raise_for_status()
                logger.info(f"✅ [{app_type.upper()}] Indexador KITSUNARR actualizado correctamente.")
            else:
                post_resp = await client.post(f"{clean_app_url}/api/v3/indexer", json=payload, headers=headers)
                post_resp.raise_for_status()
                logger.info(f"✅ [{app_type.upper()}] Indexador KITSUNARR creado e inyectado correctamente.")
                
            return {"success": True}
            
    except httpx.HTTPStatusError as e:
        error_text = e.response.text
        logger.error(f"❌ Error HTTP sincronizando con {app_type.upper()}: {error_text}")
        
        if "401:Unauthorized" in error_text or "401" in error_text:
            return {"success": False, "error": "No tienes indexador configurado en Kitsunarr. Ve a la pestaña 'Indexadores' y configúralo primero."}
            
        try:
            if await _confirm_existing_after_ambiguous_sync(clean_app_url, headers, clean_kitsunarr_url):
                logger.warning(
                    f"⚠️ [{app_type.upper()}] ARR devolvió un error tras la prueba interna, "
                    "pero el indexador KITSUNARR ya existe. Se considera sincronizado."
                )
                return {
                    "success": True,
                    "warning": "ARR guardó el indexador, aunque devolvió una advertencia durante la prueba interna."
                }
        except Exception:
            pass

        return {"success": False, "error": f"Error API ({e.response.status_code}): Verifica tu API Key o URL."}
    except Exception as e:
        try:
            if await _confirm_existing_after_ambiguous_sync(clean_app_url, headers, clean_kitsunarr_url):
                logger.warning(
                    f"⚠️ [{app_type.upper()}] La sincronización terminó con una respuesta ambigua "
                    "de red, pero el indexador KITSUNARR ya existe en ARR. Se considera sincronizado."
                )
                return {
                    "success": True,
                    "warning": "ARR aceptó el indexador, pero no confirmó la prueba interna antes del timeout."
                }
        except Exception:
            pass

        logger.error(f"❌ Error de red sincronizando con {app_type.upper()}: {type(e).__name__}: {repr(e)}")
        return {"success": False, "error": "No se pudo conectar con el servidor. Verifica la IP/Puerto."}
