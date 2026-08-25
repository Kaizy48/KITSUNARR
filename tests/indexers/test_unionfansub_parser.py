import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from core.app.indexers.snapshots import InvalidTrackerSnapshot, merge_tracker_metadata_json
from core.app.indexers.unionfansub import SCRAPE_REVISION, UnionFansubIndexer
from core.database.models import TorrentCache


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "unionfansub"


# ------------------------------------------------------------
# Lee el nombre de una fixture UTF-8 anonimizada y devuelve su HTML como str.
# Realiza únicamente lectura de filesystem, sin modificar archivos ni estado.
# Propaga FileNotFoundError, OSError y UnicodeDecodeError para fallar la suite
# de forma visible. Depende de la ruta base FIXTURES, pathlib.Path.__truediv__
# y Path.read_text; todos los tests de parser reutilizan este helper.
# ------------------------------------------------------------
def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class UnionFansubParserTests(unittest.TestCase):
    # ------------------------------------------------------------
    # Comprueba que un cache hit sin freeleech observado no invente false ni
    # claves de presencia. Recibe solo self y devuelve None; crea una fila en
    # memoria, sin I/O o persistencia. Fallos de modelo/proyección se propagan
    # y una regresión termina en AssertionError de unittest. Depende de
    # TorrentCache y _build_cached_search_result como contrato de proyección.
    # ------------------------------------------------------------
    def test_cached_unknown_freeleech_omits_presence_keys(self) -> None:
        indexer = UnionFansubIndexer()
        torrent = TorrentCache(
            guid="UNF-12345",
            source_guid="12345",
            original_title="Serie de ejemplo",
            enriched_title="Serie de ejemplo",
            tracker_freeleech=None,
            freeleech_until=None,
        )

        result = indexer._build_cached_search_result(
            torrent,
            source_guid="12345",
            original_title="Fallback",
            incoming_fansub="Fansub",
        )

        self.assertNotIn("freeleech_present", result)
        self.assertNotIn("tracker_freeleech", result)
        self.assertIsNone(result["freeleech_until"])

    # ------------------------------------------------------------
    # Comprueba que una fila legacy con fecha freeleech se proyecte como
    # presencia verdadera. Recibe solo self y devuelve None; crea datos en
    # memoria sin I/O ni persistencia. Propaga errores de modelo/proyección y
    # expresa una regresión como AssertionError. Depende de TorrentCache,
    # datetime y _build_cached_search_result.
    # ------------------------------------------------------------
    def test_cached_legacy_freeleech_date_preserves_true(self) -> None:
        indexer = UnionFansubIndexer()
        torrent = TorrentCache(
            guid="UNF-12345",
            source_guid="12345",
            original_title="Serie de ejemplo",
            enriched_title="Serie de ejemplo",
            tracker_freeleech=None,
            freeleech_until=datetime(2026, 8, 25, 12, 0),
        )

        result = indexer._build_cached_search_result(
            torrent,
            source_guid="12345",
            original_title="Fallback",
            incoming_fansub="Fansub",
        )

        self.assertIs(result["freeleech_present"], True)
        self.assertIs(result["tracker_freeleech"], True)

    # ------------------------------------------------------------
    # Verifica el snapshot completo de la fixture: identidad, bytes, UTC,
    # swarm, capturas, metadata y payload. Recibe solo self y devuelve None;
    # lee una fixture local, sin red ni persistencia. Errores de lectura,
    # InvalidTrackerSnapshot/serialización se propagan y una diferencia causa
    # AssertionError. Depende de load_fixture, _parse_ficha_snapshot,
    # TrackerSnapshot.to_payload, json y SCRAPE_REVISION.
    # ------------------------------------------------------------
    def test_valid_details_builds_typed_snapshot(self) -> None:
        indexer = UnionFansubIndexer()
        observed_at = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)

        snapshot = indexer._parse_ficha_snapshot(
            load_fixture("details_valid.html"),
            expected_source_guid="12345",
            observed_at=observed_at,
        )

        self.assertEqual(snapshot.source_guid, "12345")
        self.assertEqual(snapshot.tracker_title, "Serie de ejemplo 2")
        self.assertEqual(snapshot.size_bytes, 5_209_240_670)
        self.assertEqual(snapshot.size_source, "details_exact_bytes")
        self.assertEqual(snapshot.scrape_revision, SCRAPE_REVISION)
        self.assertEqual(indexer.SCRAPE_REVISION, SCRAPE_REVISION)
        self.assertEqual(snapshot.scraped_at, observed_at)
        self.assertIsNotNone(snapshot.published_at)
        self.assertEqual(snapshot.published_at.utcoffset(), timezone.utc.utcoffset(snapshot.published_at))
        self.assertEqual(snapshot.tracker_seeders, 15)
        self.assertEqual(snapshot.tracker_leechers, 0)
        self.assertFalse(snapshot.freeleech_present)
        self.assertIsNone(snapshot.file_entries)
        self.assertEqual(len(snapshot.screenshots or ()), 6)
        self.assertEqual(snapshot.tracker_metadata["category"], "Anime")
        self.assertEqual(snapshot.tracker_metadata["views"], 1464)
        self.assertEqual(snapshot.tracker_metadata["hits"], 479)
        self.assertEqual(snapshot.tracker_metadata["grabs"], 21)
        self.assertEqual(snapshot.tracker_metadata["file_count"], 12)
        self.assertEqual(snapshot.tracker_metadata["last_seed"], "Hace 5 minutos")
        self.assertFalse(snapshot.partial)

        payload = snapshot.to_payload()
        self.assertEqual(payload["last_scraped_at"], observed_at)
        self.assertEqual(len(json.loads(payload["screenshots_json"])), 6)
        self.assertEqual(json.loads(payload["tags"])[0:3], ["1080p", "HEVC", "Blu-ray"])
        self.assertFalse(payload["tracker_freeleech"])
        self.assertIn("tracker_metadata", payload["observed_fields"])
        self.assertNotIn("cookie", payload)

    # ------------------------------------------------------------
    # Comprueba que ZoneInfo aplique CET/CEST histórico a dos fechas absolutas.
    # Recibe solo self y devuelve None; patch.dict modifica TZ únicamente en
    # su contexto, sin red ni persistencia. Errores de zona/parseo se propagan
    # y offsets incorrectos producen AssertionError. Depende de patch.dict,
    # datetime y _parse_spanish_datetime_utc.
    # ------------------------------------------------------------
    def test_historical_dates_apply_zoneinfo_dst(self) -> None:
        indexer = UnionFansubIndexer()

        with patch.dict("os.environ", {"TZ": "Europe/Madrid"}):
            winter = indexer._parse_spanish_datetime_utc("16 Jan 2026, 23:06")
            summer = indexer._parse_spanish_datetime_utc("16 Aug 2026, 23:06")

        self.assertEqual(winter, datetime(2026, 1, 16, 22, 6, tzinfo=timezone.utc))
        self.assertEqual(summer, datetime(2026, 8, 16, 21, 6, tzinfo=timezone.utc))

    # ------------------------------------------------------------
    # Reproduce etiquetas U+FFFD y verifica que sobrevivan tamaño, fecha,
    # stats y capturas. Recibe solo self y devuelve None; lee la fixture y la
    # altera en memoria, sin ejecutar HTML, red o persistencia. Errores de
    # lectura/parser se propagan y una regresión genera AssertionError. Depende
    # de load_fixture y _parse_ficha_snapshot con su normalizador de etiquetas.
    # ------------------------------------------------------------
    def test_mhtml_replacement_labels_keep_exact_fields(self) -> None:
        indexer = UnionFansubIndexer()
        html = (
            load_fixture("details_valid.html")
            .replace("Descripción", "Descripci��n")
            .replace("Última semilla", "��ltima semilla")
            .replace("Tamaño", "Tama��o")
            .replace("Añadido", "A��adido")
        )

        snapshot = indexer._parse_ficha_snapshot(html, expected_source_guid="12345")

        self.assertEqual(snapshot.size_bytes, 5_209_240_670)
        self.assertEqual(snapshot.size_source, "details_exact_bytes")
        self.assertEqual(snapshot.tracker_metadata["file_count"], 12)
        self.assertEqual(snapshot.tracker_metadata["last_seed"], "Hace 5 minutos")
        self.assertIsNotNone(snapshot.published_at)
        self.assertEqual(len(snapshot.screenshots or ()), 6)
        self.assertFalse(snapshot.partial)

    # ------------------------------------------------------------
    # Comprueba el fallback humano cuando details no contiene bytes exactos.
    # Recibe solo self y devuelve None; lee y modifica una fixture en memoria,
    # sin red ni persistencia. Errores de lectura/parser se propagan y un valor
    # u origen incorrectos causan AssertionError. Depende de load_fixture,
    # _parse_ficha_snapshot y _parse_size_field/_parse_size_to_bytes.
    # ------------------------------------------------------------
    def test_human_size_is_a_real_fallback(self) -> None:
        indexer = UnionFansubIndexer()
        html = load_fixture("details_valid.html").replace(
            "4,85 GB (5.209.240.670 bytes)",
            "4,85 GB",
        )

        snapshot = indexer._parse_ficha_snapshot(html, expected_source_guid="12345")

        self.assertEqual(snapshot.size_bytes, int(4.85 * 1024 ** 3))
        self.assertEqual(snapshot.size_source, "details_human_binary")

    # ------------------------------------------------------------
    # Verifica que una fixture de login HTTP 200 sea rechazada como details.
    # Recibe solo self y devuelve None; lee HTML local sin red/persistencia.
    # assertRaises consume InvalidTrackerSnapshot esperado; falta de excepción
    # causa AssertionError y otros errores se propagan. Depende de load_fixture,
    # _parse_ficha_snapshot y InvalidTrackerSnapshot.
    # ------------------------------------------------------------
    def test_login_http_200_is_rejected(self) -> None:
        indexer = UnionFansubIndexer()

        with self.assertRaises(InvalidTrackerSnapshot):
            indexer._parse_ficha_snapshot(
                load_fixture("login_200.html"),
                expected_source_guid="12345",
            )

    # ------------------------------------------------------------
    # Comprueba que el GUID embebido distinto del esperado rechace la ficha.
    # Recibe solo self y devuelve None; lee una fixture local sin red ni DB.
    # assertRaises consume InvalidTrackerSnapshot, su ausencia produce
    # AssertionError y errores ajenos se propagan. Depende de load_fixture,
    # _parse_ficha_snapshot y la validación de identidad details.
    # ------------------------------------------------------------
    def test_mismatched_identity_is_rejected(self) -> None:
        indexer = UnionFansubIndexer()

        with self.assertRaises(InvalidTrackerSnapshot):
            indexer._parse_ficha_snapshot(
                load_fixture("details_valid.html"),
                expected_source_guid="54321",
            )

    # ------------------------------------------------------------
    # Verifica que una fecha ilegible quede ausente/parcial sin inventar now.
    # Recibe solo self y devuelve None; lee/modifica HTML local, no hace red o
    # DB, y el parser puede emitir un warning esperado. Errores de fixture,
    # parser o payload se propagan; diferencias causan AssertionError. Depende
    # de load_fixture, _parse_ficha_snapshot y TrackerSnapshot.to_payload.
    # ------------------------------------------------------------
    def test_invalid_published_date_never_falls_back_to_now(self) -> None:
        indexer = UnionFansubIndexer()
        html = load_fixture("details_valid.html").replace(
            "16 Aug 2026, 23:06",
            "fecha no válida",
        )

        snapshot = indexer._parse_ficha_snapshot(html, expected_source_guid="12345")

        self.assertIsNone(snapshot.published_at)
        self.assertIsNone(snapshot.to_payload()["publish_date"])
        self.assertTrue(snapshot.partial)

    # ------------------------------------------------------------
    # Verifica que una captura con host no permitido vuelva no observable la
    # colección completa. Recibe solo self y devuelve None; lee/modifica una
    # fixture local sin descargar imágenes ni escribir DB. Errores de parser o
    # serialización se propagan y una semántica distinta causa AssertionError.
    # Depende de load_fixture, _parse_ficha_snapshot, _parse_screenshots,
    # _sanitize_remote_url y TrackerSnapshot.to_payload.
    # ------------------------------------------------------------
    def test_partial_screenshot_collection_is_not_observed(self) -> None:
        indexer = UnionFansubIndexer()
        html = load_fixture("details_valid.html").replace(
            "https://galeria.unionfansub.com/mini/captura_01.jpg",
            "https://example.invalid/captura_01.jpg",
            1,
        )

        snapshot = indexer._parse_ficha_snapshot(html, expected_source_guid="12345")
        payload = snapshot.to_payload()

        self.assertIsNone(snapshot.screenshots)
        self.assertIsNone(payload["screenshots"])
        self.assertIsNone(payload["screenshots_json"])
        self.assertNotIn("screenshots", snapshot.observed_fields)
        self.assertTrue(snapshot.partial)

    # ------------------------------------------------------------
    # Verifica que una fila técnica vacía omita tags/título técnico completos
    # sin perder metadata dispersa observada. Recibe solo self y devuelve None;
    # lee/modifica una fixture, sin red o persistencia. Errores de lectura,
    # parser o payload se propagan y una regresión causa AssertionError. Depende
    # de load_fixture, _parse_ficha_snapshot y TrackerSnapshot.to_payload.
    # ------------------------------------------------------------
    def test_partial_technical_collection_does_not_emit_tags(self) -> None:
        indexer = UnionFansubIndexer()
        html = load_fixture("details_valid.html").replace(
            "<tr><td>Audio:</td><td>Japonés, FLAC</td></tr>",
            "<tr><td>Audio:</td><td></td></tr>",
        )

        snapshot = indexer._parse_ficha_snapshot(html, expected_source_guid="12345")
        payload = snapshot.to_payload()

        self.assertNotIn("tags", snapshot.observed_fields)
        self.assertIsNone(payload["tags"])
        self.assertEqual(snapshot.extra_info, "")
        self.assertNotIn("technical", snapshot.tracker_metadata)
        self.assertEqual(payload["tracker_metadata"]["views"], 1464)
        self.assertTrue(snapshot.partial)

    # ------------------------------------------------------------
    # Prueba que el merge recursivo preserve ausentes/None y aplique cero,
    # false y listas vacías. Recibe solo self y devuelve None; opera en memoria
    # sin I/O, logging ni DB. Errores JSON/merge se propagan y diferencias
    # terminan en AssertionError. Depende de json.dumps/json.loads y
    # merge_tracker_metadata_json, incluido su helper recursivo.
    # ------------------------------------------------------------
    def test_tracker_metadata_merge_preserves_absent_nested_values(self) -> None:
        current = json.dumps({
            "version": 1,
            "technical": {"video": "H.264", "audio": "AAC"},
            "views": 99,
            "flags": ["legacy"],
            "freeleech": True,
        })

        merged_json = merge_tracker_metadata_json(current, {
            "version": 1,
            "technical": {"video": "HEVC", "audio": None},
            "views": 0,
            "flags": [],
            "freeleech": False,
            "missing": None,
        })
        merged = json.loads(merged_json or "{}")

        self.assertEqual(merged["technical"], {"video": "HEVC", "audio": "AAC"})
        self.assertEqual(merged["views"], 0)
        self.assertEqual(merged["flags"], [])
        self.assertFalse(merged["freeleech"])
        self.assertNotIn("missing", merged)

    # ------------------------------------------------------------
    # Verifica presencia freeleech y vencimiento desde observed_at conocido.
    # Recibe solo self y devuelve None; lee una fixture y modifica HTML en
    # memoria, sin red ni DB. Errores de lectura/parser se propagan y una
    # presencia/fecha incorrecta causa AssertionError. Depende de load_fixture,
    # datetime y _parse_ficha_snapshot con el parser de duración freeleech.
    # ------------------------------------------------------------
    def test_freeleech_presence_is_explicit(self) -> None:
        indexer = UnionFansubIndexer()
        observed_at = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
        html = load_fixture("details_valid.html").replace(
            "</body>",
            "<p>FREEleech durante 1 día 2h 3m</p></body>",
        )

        snapshot = indexer._parse_ficha_snapshot(
            html,
            expected_source_guid="12345",
            observed_at=observed_at,
        )

        self.assertTrue(snapshot.freeleech_present)
        self.assertEqual(
            snapshot.freeleech_until,
            datetime(2026, 8, 25, 12, 3),
        )
        self.assertIn("freeleech_present", snapshot.observed_fields)

    # ------------------------------------------------------------
    # Verifica que cero y un byte sean rechazados como tamaño principal.
    # Recibe solo self y devuelve None; lee/modifica fixtures dentro de
    # subtests, sin red ni DB. assertRaises consume InvalidTrackerSnapshot;
    # no lanzarlo causa AssertionError y otros errores se propagan. Depende de
    # load_fixture, subTest, _parse_ficha_snapshot y InvalidTrackerSnapshot.
    # ------------------------------------------------------------
    def test_main_torrent_size_sentinels_are_rejected(self) -> None:
        indexer = UnionFansubIndexer()
        for sentinel in (0, 1):
            html = load_fixture("details_valid.html").replace(
                "4,85 GB (5.209.240.670 bytes)",
                f"{sentinel} Bytes",
            )
            with self.subTest(sentinel=sentinel), self.assertRaises(InvalidTrackerSnapshot):
                indexer._parse_ficha_snapshot(html, expected_source_guid="12345")

    # ------------------------------------------------------------
    # Comprueba que el AJAX local produzca doce paths/tamaños ordenados, con
    # cero y un byte válidos por archivo. Recibe solo self y devuelve None;
    # lee una fixture sin red, .torrent o persistencia. Errores de lectura,
    # parser/DTO se propagan y diferencias causan AssertionError. Depende de
    # load_fixture, _parse_filelist_response y TrackerFileEntry.
    # ------------------------------------------------------------
    def test_ajax_filelist_parses_paths_and_sizes(self) -> None:
        indexer = UnionFansubIndexer()

        entries, warning = indexer._parse_filelist_response(
            load_fixture("filelist_ajax.html"),
            expected_file_count=12,
        )

        self.assertIsNone(warning)
        self.assertIsNotNone(entries)
        self.assertEqual(len(entries or ()), 12)
        self.assertEqual([entry.path for entry in (entries or ())[:3]], [
            "Serie de ejemplo/episodio 01.mkv",
            "Serie de ejemplo/subtítulos.ass",
            "Serie de ejemplo/nota.txt",
        ])
        self.assertEqual([entry.size_bytes for entry in (entries or ())[:3]], [
            int(1.5 * 1024 ** 3),
            0,
            1,
        ])

    # ------------------------------------------------------------
    # Comprueba que un AJAX cuyo conteo difiere de details devuelva None y
    # warning. Recibe solo self y devuelve None; lee una fixture sin red o DB.
    # Errores de lectura/parser se propagan y un resultado diferente causa
    # AssertionError. Depende de load_fixture, _parse_filelist_response y la
    # validación expected_file_count.
    # ------------------------------------------------------------
    def test_ajax_filelist_count_mismatch_is_rejected(self) -> None:
        indexer = UnionFansubIndexer()

        entries, warning = indexer._parse_filelist_response(
            load_fixture("filelist_ajax.html"),
            expected_file_count=11,
        )

        self.assertIsNone(entries)
        self.assertIsNotNone(warning)
        self.assertIn("se anunciaron 11", warning or "")
        self.assertIn("se obtuvieron 12", warning or "")

    # ------------------------------------------------------------
    # Comprueba que 5001 rutas no se publiquen como 5000 entradas completas.
    # Recibe solo self y devuelve None; genera HTML grande en memoria sin red
    # o DB. Errores de asignación/parser se propagan y un resultado distinto
    # causa AssertionError. Depende de _parse_filelist_response,
    # _parse_file_entries_container y su límite defensivo.
    # ------------------------------------------------------------
    def test_ajax_filelist_over_limit_is_not_observed(self) -> None:
        indexer = UnionFansubIndexer()
        rows = "".join(
            f"<tr><td>ruta/{item}.bin</td><td>1 Byte</td></tr>"
            for item in range(5001)
        )

        entries, warning = indexer._parse_filelist_response(
            f"<table>{rows}</table>",
            expected_file_count=5001,
        )

        self.assertIsNone(entries)
        self.assertIn("supera 5000", warning or "")


class UnionFansubHTTPContractTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------
    # Verifica que rehydrate reutilice el AsyncClient prestado en tres recursos,
    # lo deje abierto y omita cookies del payload. Recibe solo self y devuelve
    # None; lee fixtures y usa MockTransport/memoria sin red real ni DB. Errores
    # HTTP/parser/callback se propagan y las expectativas fallan con
    # AssertionError. Depende de load_fixture, httpx.AsyncClient/MockTransport,
    # el handler anidado y rehydrate_torrent.
    # ------------------------------------------------------------
    async def test_rehydrate_reuses_supplied_client(self) -> None:
        details_html = load_fixture("details_valid.html")
        filelist_html = load_fixture("filelist_ajax.html")
        requested_paths = []

        # ------------------------------------------------------------
        # Simula tres recursos: recibe httpx.Request y devuelve httpx.Response,
        # usando details_html/filelist_html capturados. Añade la ruta a
        # requested_paths, sin red, DB o logging; una ruta inesperada devuelve
        # 404. Errores al inspeccionar URL o construir Response se propagan.
        # Depende del contrato de httpx.MockTransport y de las fixtures locales.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path.endswith("/details.php"):
                return httpx.Response(200, text=details_html)
            if request.url.path.endswith("/filelist.php"):
                return httpx.Response(200, text=filelist_html)
            if request.url.path.endswith("/showthread.php"):
                return httpx.Response(
                    200,
                    text='<div class="portada"><img src="https://galeria.unionfansub.com/poster.jpg"></div>',
                )
            return httpx.Response(404)

        indexer = UnionFansubIndexer()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            payload = await indexer.rehydrate_torrent(
                "12345",
                "cookie-de-prueba",
                "Fallback",
                client=client,
            )
            self.assertFalse(client.is_closed)

        self.assertIsNotNone(payload)
        self.assertEqual(requested_paths, [
            "/details.php",
            "/filelist.php",
            "/showthread.php",
        ])
        self.assertEqual(payload["size_bytes"], 5_209_240_670)
        self.assertEqual(len(payload["file_entries"]), 12)
        self.assertEqual(payload["poster_url"], "https://galeria.unionfansub.com/poster.jpg")
        self.assertEqual(payload["scrape_revision"], SCRAPE_REVISION)
        self.assertNotIn("cookie", payload)

    # ------------------------------------------------------------
    # Comprueba que un AJAX truncado preserve file_entries no observado y marque
    # partial+warning. Recibe solo self y devuelve None; lee fixture, usa
    # MagicMock/AsyncMock y parchea portada, sin red real ni DB. Errores de
    # parser/mock se propagan y las expectativas producen AssertionError.
    # Depende de load_fixture, httpx.Response, patch.object,
    # _parse_ficha_snapshot y _complete_optional_snapshot_resources.
    # ------------------------------------------------------------
    async def test_incomplete_filelist_is_not_marked_observed(self) -> None:
        indexer = UnionFansubIndexer()
        snapshot = indexer._parse_ficha_snapshot(
            load_fixture("details_valid.html"),
            expected_source_guid="12345",
        )
        request = httpx.Request(
            "GET",
            "https://torrent.unionfansub.com/filelist.php?id=12345&ajax=1",
        )
        incomplete_html = (
            "<table><tr><td>Serie de ejemplo/episodio 01.mkv</td>"
            "<td>1,50 GB</td></tr></table>"
        )
        fake_client = MagicMock()
        fake_client.get = AsyncMock(
            return_value=httpx.Response(200, text=incomplete_html, request=request)
        )

        with patch.object(
            indexer,
            "get_poster",
            new=AsyncMock(return_value="https://galeria.unionfansub.com/poster.jpg"),
        ):
            completed = await indexer._complete_optional_snapshot_resources(
                snapshot,
                "cookie-de-prueba",
                fake_client,
            )

        self.assertIsNone(completed.file_entries)
        self.assertNotIn("file_entries", completed.observed_fields)
        self.assertTrue(completed.partial)
        self.assertTrue(any("se anunciaron 12" in item for item in completed.warnings))

    # ------------------------------------------------------------
    # Verifica que rehydrate rechace details sin tamaño antes de filelist/foro.
    # Recibe solo self y devuelve None; lee/modifica fixture y usa MockTransport,
    # sin red real ni DB, registrando rutas en memoria. Errores HTTP/parser o
    # callback se propagan y las expectativas fallan con AssertionError.
    # Depende de load_fixture, httpx.AsyncClient/MockTransport, el handler
    # anidado y rehydrate_torrent.
    # ------------------------------------------------------------
    async def test_rehydrate_rejects_details_without_size(self) -> None:
        details_html = load_fixture("details_valid.html").replace(
            "4,85 GB (5.209.240.670 bytes)",
            "tamaño desconocido",
        )
        requested_paths = []

        # ------------------------------------------------------------
        # Sirve details sin tamaño: recibe httpx.Request y devuelve Response,
        # usando details_html capturado. Añade la ruta a requested_paths, sin
        # red, DB o logging, y responde 404 a cualquier recurso adicional.
        # Errores de URL/Response se propagan. Depende de httpx.MockTransport,
        # httpx.Response y de las variables capturadas por el closure.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path.endswith("/details.php"):
                return httpx.Response(200, text=details_html)
            return httpx.Response(404)

        indexer = UnionFansubIndexer()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            payload = await indexer.rehydrate_torrent(
                "12345",
                "cookie-de-prueba",
                "Fallback",
                client=client,
            )

        self.assertIsNone(payload)
        self.assertEqual(requested_paths, ["/details.php"])

    # ------------------------------------------------------------
    # Verifica que rehydrate con ficha técnica parcial omita tags/título
    # enriquecido. Recibe solo self y devuelve None; lee/modifica fixtures y
    # usa MockTransport, sin red real ni DB. Errores HTTP/parser/callback se
    # propagan y diferencias causan AssertionError. Depende de load_fixture,
    # httpx.AsyncClient/MockTransport, el handler anidado y rehydrate_torrent.
    # ------------------------------------------------------------
    async def test_rehydrate_partial_technical_data_omits_enriched_title(self) -> None:
        details_html = load_fixture("details_valid.html").replace(
            "<tr><td>Audio:</td><td>Japonés, FLAC</td></tr>",
            "<tr><td>Audio:</td><td></td></tr>",
        )
        filelist_html = load_fixture("filelist_ajax.html")

        # ------------------------------------------------------------
        # Devuelve respuestas locales: recibe httpx.Request y retorna Response
        # desde details_html/filelist_html capturados. No muta estado, hace red
        # o logging; una ruta inesperada retorna 404. Errores de URL/Response se
        # propagan. Depende de httpx.MockTransport/httpx.Response y de las
        # fixtures capturadas por el closure.
        # ------------------------------------------------------------
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/details.php"):
                return httpx.Response(200, text=details_html)
            if request.url.path.endswith("/filelist.php"):
                return httpx.Response(200, text=filelist_html)
            if request.url.path.endswith("/showthread.php"):
                return httpx.Response(
                    200,
                    text='<div class="portada"><img src="https://galeria.unionfansub.com/poster.jpg"></div>',
                )
            return httpx.Response(404)

        indexer = UnionFansubIndexer()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            payload = await indexer.rehydrate_torrent(
                "12345",
                "cookie-de-prueba",
                "Fallback",
                client=client,
            )

        self.assertIsNotNone(payload)
        self.assertIsNone(payload["tags"])
        self.assertNotIn("tags", payload["observed_fields"])
        self.assertNotIn("title", payload)
        self.assertTrue(payload["partial"])

    # ------------------------------------------------------------
    # Verifica que el fallback cree, reutilice y cierre un único AsyncClient.
    # Recibe solo self y devuelve None; lee fixtures y parchea el constructor
    # con mocks en memoria, sin red real ni DB. Errores de mock/parser se
    # propagan y conteos/lifecycle incorrectos causan AssertionError. Depende de
    # load_fixture, MagicMock/AsyncMock, patch, fake_get y rehydrate_torrent.
    # ------------------------------------------------------------
    async def test_rehydrate_creates_and_closes_one_fallback_client(self) -> None:
        details_html = load_fixture("details_valid.html")
        filelist_html = load_fixture("filelist_ajax.html")
        requested_paths = []

        # ------------------------------------------------------------
        # Simula AsyncClient.get: recibe url/kwargs y devuelve httpx.Response
        # con Request asociado, usando las fixtures capturadas. Añade la ruta a
        # requested_paths, sin red, DB o logging; destinos inesperados dan 404.
        # Errores de URL o construcción HTTPX se propagan. Depende de
        # httpx.Request/httpx.Response y details_html/filelist_html del closure.
        # ------------------------------------------------------------
        def fake_get(url, **kwargs) -> httpx.Response:
            request = httpx.Request("GET", url)
            requested_paths.append(request.url.path)
            if request.url.path.endswith("/details.php"):
                return httpx.Response(200, text=details_html, request=request)
            if request.url.path.endswith("/filelist.php"):
                return httpx.Response(200, text=filelist_html, request=request)
            if request.url.path.endswith("/showthread.php"):
                return httpx.Response(
                    200,
                    text='<div class="portada"><img src="https://galeria.unionfansub.com/poster.jpg"></div>',
                    request=request,
                )
            return httpx.Response(404, request=request)

        fake_client = MagicMock()
        fake_client.get = AsyncMock(side_effect=fake_get)
        fake_client.aclose = AsyncMock()
        indexer = UnionFansubIndexer()

        with patch(
            "core.app.indexers.unionfansub.httpx.AsyncClient",
            return_value=fake_client,
        ) as client_constructor:
            payload = await indexer.rehydrate_torrent(
                "12345",
                "cookie-de-prueba",
                "Fallback",
            )

        self.assertIsNotNone(payload)
        self.assertEqual(client_constructor.call_count, 1)
        fake_client.aclose.assert_awaited_once()
        self.assertEqual(requested_paths, [
            "/details.php",
            "/filelist.php",
            "/showthread.php",
        ])


if __name__ == "__main__":
    unittest.main()
