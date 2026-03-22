# ==========================================
# IMPORTS Y CONFIGURACIÓN INICIAL
# ==========================================
import httpx
from core.logger import logger

# ==========================================
# GESTOR DE SINCRONIZACIÓN DE INDEXADORES
# ==========================================

"""
Conecta con la API v3 de Sonarr o Radarr para inyectar o actualizar Kitsunarr 
como un indexador Torznab personalizado con permisos completos de búsqueda y RSS.
Intercepta errores comunes como la falta de configuración del tracker interno para 
devolver mensajes amigables al usuario.
"""
async def sync_indexer_to_arr(app_type: str, app_url: str, app_api_key: str, kitsunarr_url: str, kitsunarr_api_key: str) -> dict:
    clean_app_url = app_url.rstrip("/")
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
            {"name": "categories", "value": [5000, 5070]},
            {"name": "animeCategories", "value": [5070]}
        ])
    else:
        fields.append({"name": "categories", "value": [2000]})
        
    payload = {
        "name": "Kitsunarr",
        "implementation": "Torznab",
        "configContract": "TorznabSettings",
        "protocol": "torrent",
        "priority": 25,
        "enableRss": True,
        "enableAutomaticSearch": True,
        "enableInteractiveSearch": True,
        "fields": fields
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            get_resp = await client.get(f"{clean_app_url}/api/v3/indexer", headers=headers)
            get_resp.raise_for_status()
            
            indexers = get_resp.json()
            existing_indexer = next((i for i in indexers if i.get("name") == "Kitsunarr"), None)
            
            if existing_indexer:
                existing_indexer["fields"] = fields
                existing_indexer["priority"] = 25
                existing_indexer["enableRss"] = True
                existing_indexer["enableAutomaticSearch"] = True
                existing_indexer["enableInteractiveSearch"] = True
                
                put_resp = await client.put(f"{clean_app_url}/api/v3/indexer/{existing_indexer['id']}", json=existing_indexer, headers=headers)
                put_resp.raise_for_status()
                logger.info(f"✅ [{app_type.upper()}] Indexador Kitsunarr actualizado correctamente.")
            else:
                post_resp = await client.post(f"{clean_app_url}/api/v3/indexer", json=payload, headers=headers)
                post_resp.raise_for_status()
                logger.info(f"✅ [{app_type.upper()}] Indexador Kitsunarr creado e inyectado correctamente.")
                
            return {"success": True}
            
    except httpx.HTTPStatusError as e:
        error_text = e.response.text
        logger.error(f"❌ Error HTTP sincronizando con {app_type.upper()}: {error_text}")
        
        if "401:Unauthorized" in error_text or "401" in error_text:
            return {"success": False, "error": "No tienes indexador configurado en Kitsunarr. Ve a la pestaña 'Indexadores' y configúralo primero."}
            
        return {"success": False, "error": f"Error API ({e.response.status_code}): Verifica tu API Key o URL."}
    except Exception as e:
        logger.error(f"❌ Error de red sincronizando con {app_type.upper()}: {e}")
        return {"success": False, "error": "No se pudo conectar a la instancia. Verifica la URL y que la aplicación esté encendida."}