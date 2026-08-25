import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RehydrateFrontendStaticContractTests(unittest.TestCase):
    # ------------------------------------------------------------
    # Revisa sin entradas el JavaScript entregado y devuelve None tras impedir que
    # reaparezcan reanudación, polling solapado o progreso por lote. Solo lee el
    # repositorio; FileNotFoundError/UnicodeError y fallos de aserción se propagan.
    # Depende de ROOT, Path.read_text y de los literales del contrato de cache.js.
    # ------------------------------------------------------------
    def test_rehydrate_uses_terminal_cancel_and_chained_polling(self):
        source = (ROOT / "static/js/views/cache.js").read_text(encoding="utf-8")

        self.assertNotIn("/resume", source)
        self.assertNotIn("resumeRehydrateJob", source)
        self.assertNotIn("setInterval(", source)
        self.assertNotIn("job.batch_percent", source)
        self.assertIn("/api/ui/cache/rehydrate/active", source)
        self.assertIn("job.percent_total", source)
        self.assertIn("setTimeout(pollRehydrateStatus", source)

    # ------------------------------------------------------------
    # Inspecciona sin entradas el código y devuelve None al fijar la idempotencia
    # del POST ambiguo y la separación entre not-found y errores transitorios. Solo
    # lee cache.js; errores de lectura y aserción se propagan. Depende de ROOT,
    # Path.read_text y de los nombres estables del lifecycle frontend.
    # ------------------------------------------------------------
    def test_start_retry_is_idempotent_and_status_errors_stay_recoverable(self):
        source = (ROOT / "static/js/views/cache.js").read_text(encoding="utf-8")

        self.assertIn("payload.request_id = createRehydrateRequestId()", source)
        self.assertIn("window.crypto.getRandomValues(randomBytes)", source)
        self.assertIn("body: JSON.stringify(payload)", source)
        self.assertIn("rehydrate_job_not_found", source)
        self.assertIn("isRehydrateStatusNotFoundResponse(response, data)", source)
        self.assertIn("El servidor no pudo devolver el progreso", source)

    # ------------------------------------------------------------
    # Revisa sin entradas cache.js y devuelve None tras verificar que selected
    # sobreviva a N=0 y que solo se retiren GUID confirmados. No modifica archivos;
    # errores de lectura/aserción se propagan. Depende de ROOT, Path.read_text y
    # de los identificadores estables de selección y borrado masivo.
    # ------------------------------------------------------------
    def test_scope_preference_and_bulk_delete_keep_unconfirmed_selection(self):
        source = (ROOT / "static/js/views/cache.js").read_text(encoding="utf-8")

        self.assertIn("window.rehydratePreferredScope", source)
        self.assertNotIn("rehydrateScopeExplicitChoice", source)
        self.assertIn("hiddenSelectedCount", source)
        self.assertIn("response.ok && data.success === true", source)
        self.assertIn("if (!result.success) continue", source)
        self.assertIn("siguen seleccionados para reintentar", source)

    # ------------------------------------------------------------
    # Inspecciona sin entradas el control de foco y devuelve None tras exigir que
    # Tab se recapture y Detener no conserve foco al deshabilitarse. Solo lee
    # cache.js; errores de lectura/aserción se propagan. Depende de ROOT,
    # Path.read_text y de los marcadores estables del diálogo accesible.
    # ------------------------------------------------------------
    def test_dialog_recaptures_focus_and_moves_it_from_disabled_cancel(self):
        source = (ROOT / "static/js/views/cache.js").read_text(encoding="utf-8")

        self.assertIn("!dialog.contains(activeElement)", source)
        self.assertIn("moveFocusFromCancel", source)
        self.assertIn("if (moveFocusFromCancel && closeButton) closeButton.focus()", source)

    # ------------------------------------------------------------
    # Lee sin entradas cache.html y devuelve None tras revisar semántica accesible,
    # paleta y ausencia de tags.js duplicado. No modifica la plantilla; errores de
    # lectura/aserción se propagan. Depende de ROOT, Path.read_text y del contrato
    # de atributos, nombres y colores de la WebUI.
    # ------------------------------------------------------------
    def test_dialog_is_accessible_and_keeps_kitsunarr_palette(self):
        template = (ROOT / "templates/views/cache.html").read_text(encoding="utf-8")

        self.assertIn('role="dialog"', template)
        self.assertIn('aria-modal="true"', template)
        self.assertIn('role="progressbar"', template)
        self.assertIn('name="rehydrate_scope"', template)
        self.assertIn('name="rehydrate_policy"', template)
        self.assertIn("purple", template)
        self.assertIn("yellow", template)
        self.assertNotIn("cyan", template.lower())
        self.assertNotIn("js/core/tags.js", template)

    # ------------------------------------------------------------
    # Sin entradas, lee tres plantillas y devuelve None tras impedir que vuelvan a
    # cargar utils/tags ya incluidos por base.html. No modifica archivos ni ejecuta
    # navegador; errores de lectura y aserción se propagan. Depende de ROOT,
    # Path.read_text, subTest y de las rutas estables de ambos scripts core.
    # ------------------------------------------------------------
    def test_views_do_not_reload_core_scripts_from_base(self):
        template_names = (
            "edit_torrent.html",
            "tracker_torrent.html",
            "series.html",
        )

        for template_name in template_names:
            with self.subTest(template=template_name):
                template = (
                    ROOT / "templates/views" / template_name
                ).read_text(encoding="utf-8")
                self.assertNotIn("js/core/utils.js", template)
                self.assertNotIn("js/core/tags.js", template)

    # ------------------------------------------------------------
    # Verifica que el helper global rotula tamaños con unidades binarias IEC y
    # locale español, y que torrent_lab no mantiene otro conversor divergente.
    # No recibe ni devuelve datos ni altera archivos; lee ambas fuentes y falla
    # mediante asserts si la WebUI diverge del contrato visual confirmado.
    # Depende de ROOT, Path.read_text y de los literales estables de formatBytes.
    # ------------------------------------------------------------
    def test_webui_size_formatter_uses_binary_iec_units(self):
        source = (ROOT / "static/js/core/utils.js").read_text(encoding="utf-8")
        torrent_lab_source = (ROOT / "static/js/views/torrent_lab.js").read_text(encoding="utf-8")

        self.assertIn("const k = 1024", source)
        self.assertIn("['Bytes', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']", source)
        self.assertIn("toLocaleString('es-ES'", source)
        self.assertNotIn("const k = 1000", source)
        self.assertNotIn("function formatBytes", torrent_lab_source)

    # ------------------------------------------------------------
    # Sin entradas, inspecciona cuatro fuentes y devuelve None tras fijar la frontera
    # XSS: toasts usan texto y datos tracker pasan por escapeHtml antes de innerHTML.
    # No modifica archivos; errores de lectura/aserción se propagan y un E2E sigue
    # fuera de alcance. Depende de ROOT, Path.read_text y los marcadores de escape.
    # ------------------------------------------------------------
    def test_tracker_snapshot_text_is_escaped_before_html_rendering(self):
        utils = (ROOT / "static/js/core/utils.js").read_text(encoding="utf-8")
        cache = (ROOT / "static/js/views/cache.js").read_text(encoding="utf-8")
        detail = (ROOT / "static/js/views/tracker_torrent.js").read_text(encoding="utf-8")
        editor = (ROOT / "static/js/views/edit_torrent.js").read_text(encoding="utf-8")

        self.assertIn("function escapeHtml(value)", utils)
        self.assertIn("document.createTextNode(String(message ?? ''))", utils)
        self.assertIn("const safeDisplayName = escapeHtml(displayName)", cache)
        self.assertIn("const safeFilename = escapeHtml(filename)", detail)
        self.assertIn("const safeFilename = escapeHtml(filename)", editor)

    # ------------------------------------------------------------
    # Lee la ficha/JS sin entradas y devuelve None tras fijar las dos únicas
    # acciones visibles del metainfo: enviar al cliente o descargarlo. Impide que
    # reaparezca el cálculo manual de hash en WebUI y verifica el endpoint nuevo;
    # solo lee archivos y depende de ROOT/Path.read_text.
    # ------------------------------------------------------------
    def test_torrent_detail_exposes_send_or_download_without_manual_hash(self):
        template = (
            ROOT / "templates/views/tracker_torrent.html"
        ).read_text(encoding="utf-8")
        source = (
            ROOT / "static/js/views/tracker_torrent.js"
        ).read_text(encoding="utf-8")

        self.assertEqual(template.count("Mandar al cliente"), 1)
        self.assertEqual(template.count("Descargar .torrent"), 1)
        self.assertNotIn("Obtener Hash", template)
        self.assertNotIn("btn_calc_hash", template)
        self.assertNotIn("calculateHash", source)
        self.assertIn("sendTorrentToClient", source)
        self.assertIn("/send_to_client", source)


if __name__ == "__main__":
    unittest.main()
