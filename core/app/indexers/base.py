from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

# ------------------------------------------------------------
# Contrato común que todos los indexadores de Kitsunarr deben cumplir
# para que búsquedas, login, portadas y descargas funcionen de forma
# uniforme desde la aplicación.
# ------------------------------------------------------------
class BaseIndexer(ABC):
    # ------------------------------------------------------------
    # Identificador interno único del indexador usado en configuración,
    # caché y rutas de Kitsunarr.
    # ------------------------------------------------------------
    @property
    @abstractmethod
    def identifier(self) -> str:
        pass

    # ------------------------------------------------------------
    # Nombre visible del indexador que se muestra al usuario en la UI y
    # en mensajes de estado.
    # ------------------------------------------------------------
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    # ------------------------------------------------------------
    # Realiza la autenticación del usuario en el tracker y devuelve una
    # cookie válida para que Kitsunarr pueda raspar contenido.
    # ------------------------------------------------------------
    @abstractmethod
    async def login(self, username: str, password: str) -> Optional[str]:
        pass

    # ------------------------------------------------------------
    # Comprueba si la cookie o credencial guardada permite conectar con
    # el tracker desde Kitsunarr.
    # ------------------------------------------------------------
    @abstractmethod
    async def test_connection(self, cookie_string: str) -> bool:
        pass

    # ------------------------------------------------------------
    # Ejecuta una búsqueda en el tracker y devuelve resultados en el
    # formato estándar que Kitsunarr usa para caché y Torznab.
    # ------------------------------------------------------------
    @abstractmethod
    async def search(self, query: str, cookie_string: str) -> List[Dict[str, Any]]:
        pass

    # ------------------------------------------------------------
    # Obtiene la portada asociada a una ficha del tracker para mostrarla
    # en la biblioteca y detalles de Kitsunarr.
    # ------------------------------------------------------------
    @abstractmethod
    async def get_poster(self, url: str, cookie_string: str) -> Optional[str]:
        pass
