import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bencodepy
import httpx
from sqlmodel import Session, SQLModel, create_engine, select

import api.downloads.download as download_module
from core.app.indexers.unionfansub import UnionFansubIndexer
from core.database.models import IndexerConfig, SystemConfig, TorrentCache


# ------------------------------------------------------------
# Construye metainfo bencode mínimo y devuelve bytes válidos para las pruebas.
# No hace I/O, red ni muta estado externo; bencodepy puede propagar un error de
# serialización. Depende de un diccionario info estable para verificar el hash.
# ------------------------------------------------------------
def build_test_torrent() -> bytes:
    return bencodepy.encode({
        b"announce": b"https://tracker.invalid/announce",
        b"info": {
            b"length": 1234,
            b"name": b"test.mkv",
            b"piece length": 16384,
            b"pieces": b"x" * 20,
        },
    })


class TorrentProxyDownloadTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------
    # Ejecuta el proxy con API key y sesión web sobre SQLite temporal y devuelve
    # None tras verificar bytes, hash y Content-Disposition Unicode compatible.
    # Crea/dispone DB y mocks, sin red ni secretos; errores async/SQL/cabecera o
    # asserts se propagan. Depende de proxy_download_torrent, SQLModel y patch.
    # ------------------------------------------------------------
    async def test_sonarr_and_webui_download_unicode_title_successfully(self) -> None:
        torrent_bytes = build_test_torrent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "downloads.db"
            test_engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            SQLModel.metadata.create_all(test_engine)
            with Session(test_engine) as session:
                session.add(SystemConfig(api_key="test-api-key"))
                session.add(IndexerConfig(
                    identifier="unionfansub",
                    name="Union Fansub",
                    cookie_string="tracker-cookie",
                ))
                session.add(TorrentCache(
                    guid="UNF-12714",
                    source_guid="12714",
                    original_title="[Union Fansub | PuyaSubs!] 5-toubun no Hanayome ∬",
                    enriched_title="5-toubun no Hanayome S02",
                    size_bytes=17_571_752_882,
                    indexer="unionfansub",
                ))
                session.commit()

            # ------------------------------------------------------------
            # Simula el tracker y devuelve metainfo solo cuando la Session inicial
            # ya está cerrada. Recibe GUID/cookie y no hace red/DB; los asserts o
            # un lifecycle incorrecto fallan la prueba. Depende de test_engine.
            # ------------------------------------------------------------
            async def fake_download(source_guid: str, cookie: str) -> bytes:
                self.assertEqual(test_engine.pool.checkedout(), 0)
                self.assertEqual(source_guid, "12714")
                self.assertEqual(cookie, "tracker-cookie")
                return torrent_bytes

            fake_indexer = SimpleNamespace(
                name="Union Fansub",
                download_torrent=AsyncMock(side_effect=fake_download),
            )
            sonarr_request = SimpleNamespace(cookies={})
            webui_request = SimpleNamespace(cookies={"kitsunarr_session": "valid-session"})

            with (
                patch.object(download_module, "engine", test_engine),
                patch.object(
                    download_module.indexer_manager,
                    "get_indexer",
                    return_value=fake_indexer,
                ),
                patch.object(download_module, "decrypt_secret", side_effect=lambda value: value),
                patch.object(download_module.jwt, "decode", return_value={"sub": "admin"}),
            ):
                sonarr_response = await download_module.proxy_download_torrent(
                    "UNF-12714",
                    "base",
                    sonarr_request,
                    apikey="test-api-key",
                )
                webui_response = await download_module.proxy_download_torrent(
                    "UNF-12714",
                    "base",
                    webui_request,
                    apikey=None,
                )

            for response in (sonarr_response, webui_response):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.body, torrent_bytes)
                disposition = response.headers["content-disposition"]
                disposition.encode("ascii")
                self.assertIn(
                    'filename="[Union Fansub _ PuyaSubs_] 5-toubun no Hanayome.torrent"',
                    disposition,
                )
                self.assertIn("filename*=UTF-8''", disposition)
                self.assertIn("%E2%88%AC", disposition)

            expected_info_hash = hashlib.sha1(
                bencodepy.encode(bencodepy.decode(torrent_bytes)[b"info"])
            ).hexdigest()
            with Session(test_engine) as session:
                stored = session.exec(
                    select(TorrentCache).where(TorrentCache.guid == "UNF-12714")
                ).first()
                self.assertEqual(stored.info_hash, expected_info_hash)

            self.assertEqual(fake_indexer.download_torrent.await_count, 2)
            self.assertEqual(test_engine.pool.checkedout(), 0)
            test_engine.dispose()

    # ------------------------------------------------------------
    # Recibe solo self y devuelve None tras construir una cabecera con controles,
    # rutas y Unicode. No hace I/O ni muta estado; errores de sanitización o
    # aserción se propagan. Depende de _build_content_disposition y ASCII encode.
    # ------------------------------------------------------------
    def test_content_disposition_cannot_inject_headers_or_paths(self) -> None:
        disposition = download_module._build_content_disposition(
            'Anime/Final\r\nX-Test: injected "∬"',
            "UNF-safe",
        )

        disposition.encode("ascii")
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)
        self.assertNotIn("/", disposition)
        self.assertIn("filename*=UTF-8''", disposition)


class UnionFansubTorrentDownloadTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------
    # Simula un 404 en torrent= y metainfo válido en id=, y devuelve None tras
    # demostrar que el fallback se intenta. No hace red/DB; parchea AsyncClient
    # y registra queries. Errores HTTP/mock o asserts se propagan. Depende de
    # UnionFansubIndexer.download_torrent, httpx.Response y build_test_torrent.
    # ------------------------------------------------------------
    async def test_http_error_in_primary_candidate_does_not_skip_fallback(self) -> None:
        torrent_bytes = build_test_torrent()
        requested_queries = []

        # ------------------------------------------------------------
        # Recibe URL/kwargs y devuelve 404 para torrent= o metainfo para id=.
        # Añade la query observada en memoria, sin red/DB; URL inesperada falla.
        # Depende de httpx.Request/Response y torrent_bytes del closure.
        # ------------------------------------------------------------
        async def fake_get(url: str, **kwargs) -> httpx.Response:
            request = httpx.Request("GET", url)
            requested_queries.append(request.url.query.decode())
            if "torrent=" in request.url.query.decode():
                return httpx.Response(404, request=request)
            if "id=" in request.url.query.decode():
                return httpx.Response(
                    200,
                    content=torrent_bytes,
                    headers={"content-type": "application/x-bittorrent"},
                    request=request,
                )
            self.fail(f"URL inesperada: {request.url.path}")

        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=fake_get)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "core.app.indexers.unionfansub.httpx.AsyncClient",
            return_value=fake_client,
        ):
            result = await UnionFansubIndexer().download_torrent(
                "12714",
                "tracker-cookie",
            )

        self.assertEqual(result, torrent_bytes)
        self.assertEqual(requested_queries, ["torrent=12714", "id=12714"])

    # ------------------------------------------------------------
    # Simula un redirect externo en la primera variante y un fallback válido, y
    # devuelve None tras comprobar que la cookie nunca se envía fuera del host
    # permitido. No hace red/DB; usa mocks en memoria. Errores de seguridad o
    # asserts se propagan. Depende del redirect seguro y build_test_torrent.
    # ------------------------------------------------------------
    async def test_external_redirect_is_rejected_before_requesting_its_target(self) -> None:
        torrent_bytes = build_test_torrent()
        requested_hosts = []

        # ------------------------------------------------------------
        # Recibe URL/kwargs, registra host y devuelve redirect externo o fallback.
        # No hace I/O ni muta fuera de la lista; destinos inesperados fallan.
        # Depende de httpx.Request/Response y torrent_bytes del closure.
        # ------------------------------------------------------------
        async def fake_get(url: str, **kwargs) -> httpx.Response:
            request = httpx.Request("GET", url)
            requested_hosts.append(request.url.host)
            query = request.url.query.decode()
            if "torrent=" in query:
                return httpx.Response(
                    302,
                    headers={"location": "https://evil.invalid/stolen"},
                    request=request,
                )
            if "id=" in query:
                return httpx.Response(200, content=torrent_bytes, request=request)
            self.fail(f"URL inesperada: {request.url}")

        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=fake_get)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "core.app.indexers.unionfansub.httpx.AsyncClient",
            return_value=fake_client,
        ):
            result = await UnionFansubIndexer().download_torrent(
                "12714",
                "tracker-cookie",
            )

        self.assertEqual(result, torrent_bytes)
        self.assertEqual(requested_hosts, ["torrent.unionfansub.com", "torrent.unionfansub.com"])


if __name__ == "__main__":
    unittest.main()
