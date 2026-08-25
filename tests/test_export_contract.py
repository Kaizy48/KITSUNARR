import unittest
from datetime import datetime

from core.app.export import rehydrate_torrent_from_import, sanitize_torrent_for_export
from core.database.models import TorrentCache


class PortableTorrentContractTests(unittest.TestCase):
    # ------------------------------------------------------------
    # Sin entradas ni salida, construye una ficha en memoria y verifica que
    # sanitize_torrent_for_export excluya URL/telemetría sin mutar DB. Depende de
    # TorrentCache/exporter; errores de modelo/serialización o asserts fallan el test.
    # ------------------------------------------------------------
    def test_export_excludes_download_credentials_and_live_telemetry(self):
        torrent = TorrentCache(
            guid="UNF-42",
            original_title="Ejemplo",
            enriched_title="Ejemplo",
            download_url="http://localhost/api/download/UNF-42_base?apikey=secret",
            peers_seeds=7,
            peers_leechs=3,
            tracker_seeders=15,
            tracker_leechers=0,
            size_bytes=5_209_240_670,
        )

        exported = sanitize_torrent_for_export(torrent)

        self.assertNotIn("download_url", exported)
        self.assertNotIn("peers_seeds", exported)
        self.assertNotIn("peers_leechs", exported)
        self.assertEqual(exported["tracker_seeders"], 15)
        self.assertEqual(exported["size_bytes"], 5_209_240_670)

    # ------------------------------------------------------------
    # Sin entradas ni salida, llama rehydrate_torrent_from_import con datos locales
    # y comprueba centinela/URL segura. No hace red ni persiste; depende del importer
    # y TorrentCache, y errores de validación o aserciones se propagan a la suite.
    # ------------------------------------------------------------
    def test_import_rejects_sentinel_size_and_rebuilds_safe_url(self):
        imported = rehydrate_torrent_from_import(
            {
                "guid": "UNF-43",
                "original_title": "Ejemplo",
                "enriched_title": "Ejemplo",
                "size_bytes": 1,
            },
            "http://kitsunarr.local",
        )

        self.assertIsNone(imported.size_bytes)
        self.assertEqual(imported.download_url, "http://kitsunarr.local/api/download/UNF-43_base")
        self.assertNotIn("apikey", imported.download_url)

    # ------------------------------------------------------------
    # Sin entradas ni salida, importa un pub_date RFC local y comprueba su
    # published_at UTC-naive. No hace I/O/persistencia; depende del importer y
    # parser temporal, y errores de modelo/parseo no capturados o asserts fallan.
    # ------------------------------------------------------------
    def test_import_maps_legacy_pub_date_without_losing_the_timestamp(self):
        imported = rehydrate_torrent_from_import(
            {
                "guid": "UNF-legacy",
                "original_title": "Ejemplo legacy",
                "enriched_title": "Ejemplo legacy",
                "pub_date": "Thu, 02 Jan 2025 03:04:00 GMT",
            },
            "http://kitsunarr.local",
        )

        self.assertEqual(imported.published_at, datetime(2025, 1, 2, 3, 4))


if __name__ == "__main__":
    unittest.main()
