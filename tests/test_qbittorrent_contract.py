from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import bencodepy
import httpx
from sqlmodel import Session, SQLModel, create_engine, select

from api.ui import torrents as torrents_api
from core.database.models import IndexerConfig, SystemConfig, TorrentCache
from services.torrent import qbittorrent as qbittorrent_api
from services.torrent.metainfo import calculate_metainfo_hash


TEST_HASH = "8f4dc504ccab90637ed4d7cd6951076d2b15757a"


# ------------------------------------------------------------
# Construye un metainfo BitTorrent mínimo para las pruebas de envío. No recibe
# entradas ni hace I/O; devuelve bytes bencode estables y propaga errores de
# serialización. Depende de bencodepy y permite verificar multipart/hash exactos.
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


class QBittorrentWebAPIContractTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------
    # Sin entradas/salida, vacía el slot de sesión antes de cada test para aislar
    # cookies y conteos. Muta solo runtime qB en memoria, no hace red/DB y deja
    # propagar errores del asyncio.Lock. Depende de invalidate_qbittorrent_session.
    # ------------------------------------------------------------
    async def asyncSetUp(self) -> None:
        await qbittorrent_api.invalidate_qbittorrent_session()

    # ------------------------------------------------------------
    # Recibe un MockTransport y devuelve una factoría que crea AsyncClient reales
    # conectados al transporte. No hace red ni conserva secretos; reenvía kwargs de
    # producción y permite inspeccionar cookies/params. Propaga errores de HTTPX.
    # ------------------------------------------------------------
    def _client_factory(self, transport: httpx.MockTransport):
        real_client = httpx.AsyncClient

        # ------------------------------------------------------------
        # Recibe kwargs del cliente de producción y devuelve AsyncClient mockeado.
        # No muta el mapping de entrada ni hace red; fuerza MockTransport y depende
        # de real_client. Argumentos incompatibles propagan TypeError.
        # ------------------------------------------------------------
        def factory(**kwargs):
            return real_client(transport=transport, **kwargs)

        return factory

    # ------------------------------------------------------------
    # Simula qB 5.2 (204 + QBT_SID_<puerto>) y dos lecturas. Devuelve None tras
    # comprobar telemetría, cookie dinámica y una sola creación de sesión cacheada.
    # No hace red/DB; usa MockTransport y fallan asserts/contrato HTTP inesperado.
    # ------------------------------------------------------------
    async def test_qbittorrent_52_cookie_is_forwarded_and_session_is_reused(self) -> None:
        calls = {"login": 0, "info": 0}

        # ------------------------------------------------------------
        # Recibe requests HTTPX y devuelve login 5.2 o ficha realista. Registra
        # conteos y comprueba Referer/cookie/hash; no hace I/O externo. Una ruta o
        # cabecera inesperada falla el test. Depende de TEST_HASH.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                calls["login"] += 1
                self.assertEqual(request.headers.get("referer"), "http://localhost:8080")
                return httpx.Response(
                    204,
                    headers={"set-cookie": "QBT_SID_8080=session-token; Path=/; HttpOnly"},
                )
            if request.url.path == "/api/v2/torrents/info":
                calls["info"] += 1
                self.assertIn("QBT_SID_8080=session-token", request.headers.get("cookie", ""))
                self.assertEqual(request.url.params.get("hashes"), TEST_HASH)
                return httpx.Response(200, json=[{
                    "hash": TEST_HASH,
                    "name": "Torrent de prueba",
                    "state": "stoppedDL",
                    "progress": 0.25,
                    "dlspeed": 100,
                    "upspeed": 50,
                    "eta": 120,
                    "num_seeds": 2,
                    "num_leechs": 1,
                    "ratio": 0.5,
                    "size": 5_209_240_670,
                }])
            self.fail(f"Ruta qBittorrent inesperada: {request.url.path}")

        transport = httpx.MockTransport(handler)
        with patch.object(
            qbittorrent_api.httpx,
            "AsyncClient",
            side_effect=self._client_factory(transport),
        ):
            first = await qbittorrent_api.get_torrent_telemetry(
                "http://localhost:8080", "admin", "secret", TEST_HASH
            )
            second = await qbittorrent_api.get_torrent_telemetry(
                "http://localhost:8080", "admin", "secret", TEST_HASH
            )

        self.assertEqual(first, second)
        self.assertEqual(first["client_status"], "stoppedDL")
        self.assertEqual(first["size"], 5_209_240_670)
        self.assertEqual(calls, {"login": 1, "info": 2})

    # ------------------------------------------------------------
    # Simula qB anterior con 200 Ok./SID y lista un torrent. Devuelve None tras
    # verificar compatibilidad retroactiva y filtrado de filas sin hash. No hace
    # red/DB; MockTransport registra que el SID legacy llega al GET.
    # ------------------------------------------------------------
    async def test_legacy_sid_cookie_remains_supported(self) -> None:
        observed_cookie = []

        # ------------------------------------------------------------
        # Recibe login/list GET y devuelve contrato legacy en memoria. Añade la
        # cookie del GET a observed_cookie; ruta inesperada falla. No hace I/O y
        # depende de TEST_HASH.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=legacy; Path=/"})
            if request.url.path == "/api/v2/torrents/info":
                observed_cookie.append(request.headers.get("cookie", ""))
                return httpx.Response(200, json=[
                    {"hash": TEST_HASH, "name": "Válido", "state": "uploading"},
                    {"name": "Sin hash"},
                ])
            self.fail(f"Ruta qBittorrent inesperada: {request.url.path}")

        transport = httpx.MockTransport(handler)
        with patch.object(
            qbittorrent_api.httpx,
            "AsyncClient",
            side_effect=self._client_factory(transport),
        ):
            rows = await qbittorrent_api.get_all_unionfansub_torrents(
                "http://localhost:8080", "admin", "secret"
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["info_hash"], TEST_HASH)
        self.assertIn("SID=legacy", observed_cookie[0])

    # ------------------------------------------------------------
    # Simula alta multipart y detención qB 5.x. Devuelve None tras comprobar que
    # viajan los bytes .torrent, filename y hash concretos con la sesión cacheada.
    # No hace red/DB ni conserva payloads; MockTransport valida ambos POST mutantes.
    # ------------------------------------------------------------
    async def test_add_and_stop_use_authenticated_qbittorrent_5_contract(self) -> None:
        torrent_bytes = build_test_torrent()
        calls = {"login": 0, "add": 0, "stop": 0}

        # ------------------------------------------------------------
        # Recibe requests en memoria y devuelve login/alta/stop válidos. Inspecciona
        # cookies, multipart y form sin I/O externo; una ruta o cuerpo incorrecto
        # falla el test. Depende de torrent_bytes, TEST_HASH y calls del closure.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                calls["login"] += 1
                return httpx.Response(
                    204,
                    headers={"set-cookie": "QBT_SID_8080=write-session; Path=/"},
                )
            self.assertIn("QBT_SID_8080=write-session", request.headers.get("cookie", ""))
            if request.url.path == "/api/v2/torrents/add":
                calls["add"] += 1
                self.assertIn("multipart/form-data", request.headers.get("content-type", ""))
                self.assertIn(b'filename="UNF-18258.torrent"', request.content)
                self.assertIn(torrent_bytes, request.content)
                return httpx.Response(200)
            if request.url.path == "/api/v2/torrents/stop":
                calls["stop"] += 1
                self.assertEqual(request.content.decode(), f"hashes={TEST_HASH}")
                return httpx.Response(200)
            self.fail(f"Ruta qBittorrent inesperada: {request.url.path}")

        transport = httpx.MockTransport(handler)
        with patch.object(
            qbittorrent_api.httpx,
            "AsyncClient",
            side_effect=self._client_factory(transport),
        ):
            await qbittorrent_api.add_torrent(
                "http://localhost:8080",
                "admin",
                "secret",
                torrent_bytes,
                "UNF-18258.torrent",
            )
            await qbittorrent_api.stop_torrent(
                "http://localhost:8080",
                "admin",
                "secret",
                TEST_HASH,
            )

        self.assertEqual(calls, {"login": 1, "add": 1, "stop": 1})

    # ------------------------------------------------------------
    # Simula un SID expirado: primer GET 403, relogin y segundo GET correcto.
    # Devuelve None tras verificar un único retry acotado y cookie renovada. No hace
    # red/DB; errores o intentos adicionales fallan mediante conteos/asserts.
    # ------------------------------------------------------------
    async def test_forbidden_session_is_renewed_once(self) -> None:
        calls = {"login": 0, "info": 0}

        # ------------------------------------------------------------
        # Recibe requests y rota token por número de login. El primer info devuelve
        # 403 y el segundo JSON válido; no hace I/O externo y una ruta inesperada
        # falla. Depende de calls y TEST_HASH del closure/módulo.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                calls["login"] += 1
                token = f"session-{calls['login']}"
                return httpx.Response(
                    204,
                    headers={"set-cookie": f"QBT_SID_8080={token}; Path=/"},
                )
            if request.url.path == "/api/v2/torrents/info":
                calls["info"] += 1
                if calls["info"] == 1:
                    self.assertIn("session-1", request.headers.get("cookie", ""))
                    return httpx.Response(403)
                self.assertIn("session-2", request.headers.get("cookie", ""))
                return httpx.Response(200, json=[{"hash": TEST_HASH, "state": "downloading"}])
            self.fail(f"Ruta qBittorrent inesperada: {request.url.path}")

        transport = httpx.MockTransport(handler)
        with patch.object(
            qbittorrent_api.httpx,
            "AsyncClient",
            side_effect=self._client_factory(transport),
        ):
            telemetry = await qbittorrent_api.get_torrent_telemetry(
                "http://localhost:8080", "admin", "secret", TEST_HASH
            )

        self.assertEqual(telemetry["client_status"], "downloading")
        self.assertEqual(calls, {"login": 2, "info": 2})

    # ------------------------------------------------------------
    # Simula ausencia confirmada y después HTTP 500. Devuelve None tras demostrar
    # que [] significa not_found pero un fallo eleva QBittorrentResponseError. No
    # hace red/DB; reinicia caché entre escenarios y usa transportes en memoria.
    # ------------------------------------------------------------
    async def test_not_found_is_distinct_from_remote_error(self) -> None:
        # ------------------------------------------------------------
        # Recibe login/info y devuelve sesión más lista vacía. No hace I/O, no muta
        # estado externo y ruta inesperada falla. Depende solo del contrato HTTPX.
        # ------------------------------------------------------------
        def missing_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(204, headers={"set-cookie": "QBT_SID_8080=ok; Path=/"})
            if request.url.path == "/api/v2/torrents/info":
                return httpx.Response(200, json=[])
            self.fail(f"Ruta inesperada: {request.url.path}")

        missing_transport = httpx.MockTransport(missing_handler)
        with patch.object(
            qbittorrent_api.httpx,
            "AsyncClient",
            side_effect=self._client_factory(missing_transport),
        ):
            missing = await qbittorrent_api.get_torrent_telemetry(
                "http://localhost:8080", "admin", "secret", TEST_HASH
            )
        self.assertIsNone(missing)

        await qbittorrent_api.invalidate_qbittorrent_session()

        # ------------------------------------------------------------
        # Recibe login/info y devuelve sesión más HTTP 500. No hace I/O ni muta
        # fuera del transport; rutas inesperadas fallan. Sirve para exigir error
        # tipado en vez de None.
        # ------------------------------------------------------------
        def error_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v2/auth/login":
                return httpx.Response(204, headers={"set-cookie": "QBT_SID_8080=ok; Path=/"})
            if request.url.path == "/api/v2/torrents/info":
                return httpx.Response(500)
            self.fail(f"Ruta inesperada: {request.url.path}")

        error_transport = httpx.MockTransport(error_handler)
        with patch.object(
            qbittorrent_api.httpx,
            "AsyncClient",
            side_effect=self._client_factory(error_transport),
        ):
            with self.assertRaises(qbittorrent_api.QBittorrentResponseError):
                await qbittorrent_api.get_torrent_telemetry(
                    "http://localhost:8080", "admin", "secret", TEST_HASH
                )


class QBittorrentTelemetryEndpointContractTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------
    # Crea DB temporal con configuración/ficha telemétrica y sustituye el engine UI.
    # No hace red; muta globals restaurados en asyncTearDown y deja propagar errores
    # SQL. Depende de SQLModel, TorrentCache y SystemConfig.
    # ------------------------------------------------------------
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "qbittorrent.db"
        self.test_engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.test_engine)
        with Session(self.test_engine) as session:
            session.add(SystemConfig(
                api_key="test-api-key",
                qbittorrent_url="http://localhost:8080",
                qbittorrent_user="admin",
                qbittorrent_password="encrypted-password",
            ))
            session.add(IndexerConfig(
                identifier="unionfansub",
                name="Union Fansub",
                cookie_string="encrypted-cookie",
            ))
            session.add(TorrentCache(
                guid="UNF-18258",
                source_guid="18258",
                original_title="Ficha",
                enriched_title="Ficha",
                info_hash=TEST_HASH,
                exists_in_client=True,
                client_status="stoppedDL",
                progress=0.5,
                download_speed=12,
                upload_speed=34,
            ))
            session.commit()
        self.original_engine = torrents_api.engine
        torrents_api.engine = self.test_engine

    # ------------------------------------------------------------
    # Restaura engine, dispone conexiones/directorio y vacía sesión qB. No recibe
    # datos ni hace red; muta globals de prueba y propaga errores de cleanup.
    # ------------------------------------------------------------
    async def asyncTearDown(self) -> None:
        torrents_api.engine = self.original_engine
        self.test_engine.dispose()
        self.temp_dir.cleanup()
        await qbittorrent_api.invalidate_qbittorrent_session()

    # ------------------------------------------------------------
    # Fuerza error qB durante el endpoint y devuelve None tras verificar Session DB
    # cerrada en el await, success=false y conservación de métricas previas. No hace
    # red; parchea descifrado/cliente. Depende del endpoint y pool SQLAlchemy.
    # ------------------------------------------------------------
    async def test_remote_error_preserves_last_known_telemetry(self) -> None:
        # ------------------------------------------------------------
        # Recibe credenciales/hash del endpoint y eleva un error tipado tras comprobar
        # que SQLite no tiene conexiones prestadas. No hace red/DB ni retorna valor;
        # asserts o QBittorrentResponseError se propagan al caller esperado.
        # ------------------------------------------------------------
        async def fail_without_open_session(*args, **kwargs):
            self.assertEqual(self.test_engine.pool.checkedout(), 0)
            raise qbittorrent_api.QBittorrentResponseError("fallo remoto de prueba")

        with (
            patch.object(torrents_api, "decrypt_secret", return_value="secret"),
            patch.object(
                torrents_api,
                "get_torrent_telemetry",
                new=AsyncMock(side_effect=fail_without_open_session),
            ),
        ):
            result = await torrents_api.get_torrent_client_telemetry("UNF-18258")

        self.assertFalse(result["success"])
        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-18258")
            ).first()
            self.assertTrue(torrent.exists_in_client)
            self.assertEqual(torrent.client_status, "stoppedDL")
            self.assertEqual(torrent.progress, 0.5)
            self.assertEqual(torrent.download_speed, 12)
            self.assertEqual(torrent.upload_speed, 34)

    # ------------------------------------------------------------
    # Ejecuta el envío UI con tracker/qB simulados y devuelve None tras verificar
    # sesiones SQLite cerradas durante cada await, hash persistido, alta única y
    # telemetría final. No hace red; muta solo la DB temporal y mocks restaurados.
    # ------------------------------------------------------------
    async def test_send_to_client_downloads_hashes_adds_and_persists_telemetry(self) -> None:
        torrent_bytes = build_test_torrent()
        expected_hash = calculate_metainfo_hash(torrent_bytes)
        telemetry = {
            "progress": 0.01,
            "client_status": "downloading",
            "download_speed": 1024,
            "upload_speed": 0,
            "eta": 120,
            "peers_seeds": 2,
            "peers_leechs": 1,
            "ratio": 0.0,
            "name": "test.mkv",
            "size": 1234,
        }

        # ------------------------------------------------------------
        # Recibe source GUID/cookie y devuelve metainfo tras exigir cero conexiones
        # SQLite prestadas. No hace red ni muta estado; asserts lifecycle fallan.
        # ------------------------------------------------------------
        async def fake_download(source_guid: str, cookie: str) -> bytes:
            self.assertEqual(self.test_engine.pool.checkedout(), 0)
            self.assertEqual(source_guid, "18258")
            self.assertEqual(cookie, "tracker-cookie")
            return torrent_bytes

        # ------------------------------------------------------------
        # Recibe alta qB y devuelve None después de validar bytes/nombre/lifecycle.
        # No hace red ni muta cliente real; los asserts se propagan al endpoint.
        # ------------------------------------------------------------
        async def fake_add(url, username, password, payload, filename) -> None:
            self.assertEqual(self.test_engine.pool.checkedout(), 0)
            self.assertEqual(payload, torrent_bytes)
            self.assertEqual(filename, "UNF-18258.torrent")

        fake_indexer = SimpleNamespace(download_torrent=AsyncMock(side_effect=fake_download))
        with (
            patch.object(torrents_api, "decrypt_secret", side_effect=lambda value: {
                "encrypted-password": "secret",
                "encrypted-cookie": "tracker-cookie",
            }.get(value, value)),
            patch.object(
                torrents_api.indexer_manager,
                "get_indexer",
                return_value=fake_indexer,
            ),
            patch.object(
                torrents_api,
                "get_torrent_telemetry",
                new=AsyncMock(side_effect=[None, telemetry]),
            ) as telemetry_mock,
            patch.object(torrents_api, "add_torrent", new=AsyncMock(side_effect=fake_add)) as add_mock,
        ):
            result = await torrents_api.send_torrent_to_client("UNF-18258")

        self.assertTrue(result["success"])
        self.assertFalse(result["already_in_client"])
        self.assertEqual(result["info_hash"], expected_hash)
        self.assertEqual(telemetry_mock.await_count, 2)
        add_mock.assert_awaited_once()
        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-18258")
            ).first()
            self.assertEqual(torrent.info_hash, expected_hash)
            self.assertTrue(torrent.exists_in_client)
            self.assertEqual(torrent.client_status, "downloading")
            self.assertEqual(torrent.progress, 0.01)

    # ------------------------------------------------------------
    # Ejecuta un envío cuyo hash ya existe y devuelve None tras verificar éxito sin
    # segundo POST multipart. Simula tracker/telemetría, no hace red y muta solo el
    # hash/estado de la DB temporal; depende de la detección exacta previa al alta.
    # ------------------------------------------------------------
    async def test_send_to_client_does_not_add_an_existing_hash_twice(self) -> None:
        torrent_bytes = build_test_torrent()
        expected_hash = calculate_metainfo_hash(torrent_bytes)
        telemetry = {
            "progress": 0.5,
            "client_status": "stoppedDL",
            "download_speed": 0,
            "upload_speed": 0,
            "eta": 60,
            "peers_seeds": 0,
            "peers_leechs": 0,
            "ratio": 0.0,
            "name": "test.mkv",
            "size": 1234,
        }
        fake_indexer = SimpleNamespace(
            download_torrent=AsyncMock(return_value=torrent_bytes)
        )

        with (
            patch.object(torrents_api, "decrypt_secret", return_value="secret"),
            patch.object(
                torrents_api.indexer_manager,
                "get_indexer",
                return_value=fake_indexer,
            ),
            patch.object(
                torrents_api,
                "get_torrent_telemetry",
                new=AsyncMock(return_value=telemetry),
            ) as telemetry_mock,
            patch.object(torrents_api, "add_torrent", new=AsyncMock()) as add_mock,
        ):
            result = await torrents_api.send_torrent_to_client("UNF-18258")

        self.assertTrue(result["success"])
        self.assertTrue(result["already_in_client"])
        self.assertEqual(result["info_hash"], expected_hash)
        telemetry_mock.assert_awaited_once()
        add_mock.assert_not_awaited()
