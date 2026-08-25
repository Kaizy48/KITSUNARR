import unittest

from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

from core.database.models import TorrentCache


class TorrentCacheSchemaTests(unittest.TestCase):
    # ------------------------------------------------------------
    # Sin entradas ni salida, crea/modifica una SQLite efímera y verifica las
    # columnas de una release limpia. Depende de SQLModel/create_engine/inspect;
    # errores SQL o aserciones incumplidas fallan la prueba, nunca la DB real.
    # ------------------------------------------------------------
    def test_clean_database_contains_scrape_contract(self):
        test_engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(test_engine)

        columns = {
            column["name"]: column
            for column in inspect(test_engine).get_columns(TorrentCache.__tablename__)
        }

        expected = {
            "tracker_title",
            "forum_url",
            "screenshots_json",
            "tracker_metadata_json",
            "file_entries_json",
            "published_at",
            "first_seen_at",
            "last_scraped_at",
            "scrape_revision",
            "description_override",
            "tags_override",
            "tracker_freeleech",
            "tracker_seeders",
            "tracker_leechers",
        }
        self.assertTrue(expected.issubset(columns))
        self.assertNotIn("pub_date", columns)
        self.assertTrue(columns["size_bytes"]["nullable"])

    # ------------------------------------------------------------
    # Sin entradas ni salida, construye solo en memoria una ficha sin tamaño,
    # freeleech, swarm ni frescura y comprueba sus None. Depende de TorrentCache,
    # no hace I/O/persistencia y cualquier validación/asercion fallida se propaga.
    # ------------------------------------------------------------
    def test_unknown_remote_values_remain_nullable(self):
        torrent = TorrentCache(
            guid="UNF-test",
            original_title="Ficha de prueba",
            enriched_title="Ficha de prueba",
        )

        self.assertIsNone(torrent.size_bytes)
        self.assertIsNone(torrent.tracker_freeleech)
        self.assertIsNone(torrent.tracker_seeders)
        self.assertIsNone(torrent.tracker_leechers)
        self.assertIsNone(torrent.last_scraped_at)


if __name__ == "__main__":
    unittest.main()
