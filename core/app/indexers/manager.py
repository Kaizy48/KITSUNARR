from typing import Dict, Optional
from core.app.indexers.base import BaseIndexer
from core.app.indexers.unionfansub import UnionFansubIndexer

# ------------------------------------------------------------
# Registro central de indexadores disponibles en Kitsunarr. Permite
# localizar el scraper correcto por identificador desde UI, Torznab
# y descargas.
# ------------------------------------------------------------
class IndexerManager:
    # ------------------------------------------------------------
    # Inicializa el registro de indexadores nativos disponibles en
    # esta instalación de Kitsunarr.
    # ------------------------------------------------------------
    def __init__(self):
        self._indexers: Dict[str, BaseIndexer] = {}
        self._register_built_in_indexers()

    # ------------------------------------------------------------
    # Registra los indexadores integrados que Kitsunarr puede usar
    # sin plugins externos.
    # ------------------------------------------------------------
    def _register_built_in_indexers(self):
        uf_indexer = UnionFansubIndexer()
        self._indexers[uf_indexer.identifier] = uf_indexer

    # ------------------------------------------------------------
    # Devuelve la instancia del indexador solicitado para que
    # Kitsunarr pueda buscar, probar conexión o descargar torrents.
    # ------------------------------------------------------------
    def get_indexer(self, identifier: str) -> Optional[BaseIndexer]:
        return self._indexers.get(identifier)

indexer_manager = IndexerManager()
