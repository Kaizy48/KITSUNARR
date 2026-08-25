import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree

from sqlmodel import Session, SQLModel, create_engine, select

import api.torznab.torznab as torznab_module
from api.torznab.torznab import generate_torznab_xml
from core.database.models import IndexerConfig, SystemConfig, TorrentCache


class TorznabSizeContractTests(unittest.TestCase):
    # ------------------------------------------------------------
    # Sin entradas ni salida, genera/parsea un RSS en memoria y verifica bytes
    # exactos, swarm y stats. No toca red/DB; depende de generate_torznab_xml y
    # ElementTree, cuyos errores o cualquier aserción fallida terminan la prueba.
    # ------------------------------------------------------------
    def test_exact_size_is_published_without_binary_conversion(self):
        xml = generate_torznab_xml(
            [{
                "guid": "UNF-18258",
                "title": "The Quintessential Quintuplets 2",
                "download_url": "http://kitsunarr/api/download/UNF-18258_base",
                "description": "Ficha de prueba",
                "size_bytes": 5_209_240_670,
                "published_at": datetime(2025, 1, 2, 3, 4),
                "tracker_seeders": 15,
                "tracker_leechers": 0,
                "tracker_metadata": {"version": 1, "stats": {"file_count": 12, "grabs": 34}},
            }],
            "quintuplets",
        )

        root = ElementTree.fromstring(xml)
        item = root.find("./channel/item")
        self.assertIsNotNone(item)
        self.assertEqual(item.find("enclosure").attrib["length"], "5209240670")

        namespace = {"torznab": "http://torznab.com/schemas/2015/feed"}
        attrs = {
            element.attrib["name"]: element.attrib["value"]
            for element in item.findall("torznab:attr", namespace)
        }
        self.assertEqual(attrs["size"], "5209240670")
        self.assertEqual(attrs["seeders"], "15")
        self.assertEqual(attrs["leechers"], "0")
        self.assertEqual(attrs["peers"], "15")
        self.assertEqual(attrs["files"], "12")
        self.assertEqual(attrs["grabs"], "34")

    # ------------------------------------------------------------
    # Sin entradas ni salida, genera/parsea un RSS con centinela y comprueba que
    # se omita. No toca red/DB; depende de generate_torznab_xml/ElementTree y
    # errores XML o aserciones incumplidas fallan la prueba.
    # ------------------------------------------------------------
    def test_unknown_size_is_omitted_instead_of_published_as_one_byte(self):
        xml = generate_torznab_xml(
            [{
                "guid": "UNF-unknown",
                "title": "Sin tamaño",
                "download_url": "http://kitsunarr/api/download/UNF-unknown_base",
                "size_bytes": 1,
            }],
            "unknown",
        )

        root = ElementTree.fromstring(xml)
        self.assertIsNone(root.find("./channel/item"))
        self.assertNotIn('length="1"', xml)

    # ------------------------------------------------------------
    # Sin entradas ni salida, genera/parsea RSS para swarm parcial/freeleech y
    # comprueba que no invente peers. No hace I/O ni muta estado; depende del
    # generador y ElementTree, y errores de XML/aserciones se propagan.
    # ------------------------------------------------------------
    def test_nullable_swarm_and_freeleech_presence_are_published_independently(self):
        xml = generate_torznab_xml(
            [{
                "guid": "UNF-partial-swarm",
                "title": "Swarm parcial",
                "download_url": "http://kitsunarr/api/download/UNF-partial-swarm_base",
                "size_bytes": 2048,
                "tracker_seeders": None,
                "tracker_leechers": 2,
                "tracker_freeleech": True,
                "freeleech_until": None,
            }],
            "partial",
        )

        root = ElementTree.fromstring(xml)
        item = root.find("./channel/item")
        namespace = {"torznab": "http://torznab.com/schemas/2015/feed"}
        attrs = {
            element.attrib["name"]: element.attrib["value"]
            for element in item.findall("torznab:attr", namespace)
        }

        self.assertEqual(attrs["leechers"], "2")
        self.assertEqual(attrs["freeleech"], "1")
        self.assertEqual(attrs["downloadvolumefactor"], "0")
        self.assertNotIn("seeders", attrs)
        self.assertNotIn("peers", attrs)

    # ------------------------------------------------------------
    # Sin entradas ni salida, inyecta controles en memoria y comprueba un RSS XML
    # 1.0 parseable. No modifica estado ni usa red/DB; depende del filtro de
    # generate_torznab_xml/ElementTree y errores XML/aserciones fallan la prueba.
    # ------------------------------------------------------------
    def test_invalid_xml10_control_characters_are_filtered_before_serializing(self):
        xml = generate_torznab_xml(
            [{
                "guid": "UNF-\u0001control",
                "title": "Anime\u0001]]> Final",
                "download_url": "http://kitsunarr/\u000bdownload",
                "description": "Descripción\u000bremota",
                "size_bytes": 4096,
                "tags": ["tag\u0001hostil"],
            }],
            "consulta\u000bhostil",
        )

        root = ElementTree.fromstring(xml)
        item = root.find("./channel/item")
        self.assertIsNotNone(item)
        self.assertNotIn("\u0001", xml)
        self.assertNotIn("\u000b", xml)
        self.assertEqual(item.find("title").text, "Anime]]> Final")
        self.assertEqual(item.find("guid").text, "UNF-control")
        self.assertEqual(item.find("enclosure").attrib["url"], "http://kitsunarr/download")


class TorznabSessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------
    # Sin entradas, ejecuta y devuelve implícitamente None tras una búsqueda con
    # SQLite temporal, verificando conexiones, Response y commit. Crea/dispone DB
    # y mocks; depende de endpoint, Session, patch y ElementTree, y cualquier error
    # async/DB/XML o aserción incumplida falla la prueba.
    # ------------------------------------------------------------
    async def test_search_closes_initial_session_before_await_and_commits_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "torznab-session.db"
            test_engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(test_engine)

            with Session(test_engine) as session:
                session.add(SystemConfig(api_key="test-api-key"))
                session.add(
                    IndexerConfig(
                        identifier="fake-indexer",
                        name="Fake Indexer",
                        cookie_string="test-cookie",
                        is_enabled=True,
                    )
                )
                session.commit()

            # ------------------------------------------------------------
            # Simula el I/O asíncrono del tracker y devuelve una ficha válida.
            # Recibe query/cookie, devuelve un resultado, no persiste y falla el
            # test si torznab_endpoint conserva una conexión SQLite durante await.
            # ------------------------------------------------------------
            async def remote_search(query, cookie):
                self.assertEqual(test_engine.pool.checkedout(), 0)
                await asyncio.sleep(0)
                self.assertEqual(test_engine.pool.checkedout(), 0)
                self.assertEqual(query, "session lifecycle")
                self.assertEqual(cookie, "test-cookie")
                return [{
                    "guid": "UNF-session-contract",
                    "source_guid": "session-contract",
                    "title": "Session Lifecycle S01",
                    "original_title": "Session Lifecycle S01",
                    "size_bytes": 8192,
                    "scraped_from_tracker": True,
                    "scrape_revision": 1,
                }]

            fake_indexer = SimpleNamespace(search_with_deadline=remote_search)
            request = SimpleNamespace(
                query_params={"t": "search", "q": "session lifecycle"},
                base_url="http://kitsunarr/",
            )

            with (
                patch.object(torznab_module, "engine", test_engine),
                patch.object(torznab_module, "is_worker_paused", return_value=False),
                patch.object(
                    torznab_module.indexer_manager,
                    "get_indexer",
                    return_value=fake_indexer,
                ),
                patch.object(torznab_module, "wake_worker"),
            ):
                response = await torznab_module.torznab_endpoint(request)

            self.assertEqual(response.status_code, 200)
            ElementTree.fromstring(response.body)
            self.assertEqual(test_engine.pool.checkedout(), 0)
            with Session(test_engine) as session:
                stored = session.exec(
                    select(TorrentCache).where(
                        TorrentCache.guid == "UNF-session-contract"
                    )
                ).first()
                self.assertIsNotNone(stored)
                self.assertEqual(stored.size_bytes, 8192)
                self.assertIsNone(stored.tracker_freeleech)

            test_engine.dispose()


if __name__ == "__main__":
    unittest.main()
