import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from api.ui import torrents as torrents_api
from core.database.models import TorrentCache


class RehydrateBackendContractTests(unittest.TestCase):
    # ------------------------------------------------------------
    # Sin entradas ni salida, crea una SQLite efímera, redirige el engine UI y
    # reinicia los slots runtime antes de cada test. Muta globals que tearDown
    # restaura, no abre red ni toca la DB real; depende de SQLModel/StaticPool y
    # asyncio.Lock, y deja propagar errores de creación de esquema/runtime.
    # ------------------------------------------------------------
    def setUp(self):
        self.test_engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.test_engine)
        self.original_engine = torrents_api.engine
        self.original_runtime = (
            torrents_api._rehydrate_jobs_lock,
            torrents_api._rehydrate_active_job,
            torrents_api._rehydrate_last_terminal_job,
            torrents_api._rehydrate_active_task,
        )
        torrents_api.engine = self.test_engine
        torrents_api._rehydrate_jobs_lock = asyncio.Lock()
        torrents_api._rehydrate_active_job = None
        torrents_api._rehydrate_last_terminal_job = None
        torrents_api._rehydrate_active_task = None

    # ------------------------------------------------------------
    # Sin entradas ni salida, restaura engine/slots y dispone la SQLite temporal
    # tras cada caso. Muta los globals de torrents_api y libera recursos; depende
    # de atributos creados por setUp y propaga errores de restauración/dispose.
    # ------------------------------------------------------------
    def tearDown(self):
        torrents_api.engine = self.original_engine
        (
            torrents_api._rehydrate_jobs_lock,
            torrents_api._rehydrate_active_job,
            torrents_api._rehydrate_last_terminal_job,
            torrents_api._rehydrate_active_task,
        ) = self.original_runtime
        self.test_engine.dispose()

    # ------------------------------------------------------------
    # Sin entradas ni salida, escribe/lee la SQLite de prueba y comprueba que
    # guardar valores efectivos, editar y resetear respeten ownership. Depende de
    # Session, CacheEditForm, update_cache_entry y _enrich_torrent_for_ui; errores
    # DB/async o aserciones incumplidas fallan la prueba.
    # ------------------------------------------------------------
    def test_manual_overrides_are_explicit_and_resettable(self):
        with Session(self.test_engine) as session:
            session.add(TorrentCache(
                guid="UNF-101",
                source_guid="101",
                original_title="Ficha 101",
                enriched_title="Ficha 101",
                description="Descripción tracker",
                tags='["Tracker"]',
            ))
            session.commit()

        unchanged_form = torrents_api.CacheEditForm(
            ai_translated_title="Ficha 101",
            description="Descripción tracker",
            tags='["Tracker"]',
        )
        asyncio.run(torrents_api.update_cache_entry("UNF-101", unchanged_form))

        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-101")
            ).first()
            self.assertIsNone(torrent.description_override)
            self.assertIsNone(torrent.tags_override)
            dto = torrents_api._enrich_torrent_for_ui(torrent)
            self.assertEqual(dto["tracker_description"], "Descripción tracker")
            self.assertEqual(dto["description"], "Descripción tracker")
            self.assertFalse(dto["has_description_override"])

        edited_form = torrents_api.CacheEditForm(
            ai_translated_title="Ficha 101",
            description="Descripción manual",
            tags='["Manual"]',
        )
        asyncio.run(torrents_api.update_cache_entry("UNF-101", edited_form))

        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-101")
            ).first()
            self.assertEqual(torrent.description_override, "Descripción manual")
            self.assertEqual(torrent.tags_override, '["Manual"]')

        reset_form = torrents_api.CacheEditForm(
            ai_translated_title="Ficha 101",
            description="Descripción tracker",
            tags='["Tracker"]',
            reset_description_override=True,
            reset_tags_override=True,
        )
        asyncio.run(torrents_api.update_cache_entry("UNF-101", reset_form))

        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-101")
            ).first()
            self.assertIsNone(torrent.description_override)
            self.assertIsNone(torrent.tags_override)

    # ------------------------------------------------------------
    # Recibe solo self y devuelve None tras fusionar scrapes con presencia
    # explícita, identidad y tamaño válidos/inválidos. Escribe/lee la SQLite de
    # prueba sin red y verifica ownership manual/qB; depende de Session,
    # TorrentCache y _merge_rehydrate_payload. Errores DB o asserts se propagan.
    # ------------------------------------------------------------
    def test_partial_merge_respects_presence_identity_and_ownership(self):
        with Session(self.test_engine) as session:
            session.add(TorrentCache(
                guid="UNF-202",
                source_guid="202",
                tracker_title="Título anterior",
                original_title="Ficha 202",
                enriched_title="Ficha 202",
                description="Descripción anterior",
                description_override="Descripción manual",
                poster_url="https://example.invalid/old.jpg",
                size_bytes=1234,
                freeleech_until=(
                    datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
                ),
                tags='["Anterior"]',
                tags_override='["Manual"]',
                tracker_seeders=12,
                tracker_leechers=3,
                peers_seeds=99,
                peers_leechs=88,
            ))
            session.commit()

        scraped_at = datetime.now(timezone.utc).replace(tzinfo=None)
        persisted, changed = torrents_api._merge_rehydrate_payload(
            "UNF-202",
            {
                "structurally_valid": True,
                "scraped_from_tracker": True,
                "source_guid": "202",
                "tracker_title": "Título nuevo",
                "description": None,
                "poster_url": "https://example.invalid/unobserved.jpg",
                "size_bytes": 1234,
                "published_at": datetime(2025, 1, 2, 3, 4, tzinfo=timezone(timedelta(hours=2))),
                "freeleech_present": False,
                "freeleech_until": None,
                "tags": "[]",
                "tracker_seeders": 0,
                "tracker_leechers": None,
                "scrape_revision": torrents_api.REHYDRATE_SCRAPE_REVISION,
                "observed_fields": [
                    "source_guid",
                    "tracker_title",
                    "description",
                    "size_bytes",
                    "published_at",
                    "freeleech_present",
                    "tags",
                    "tracker_seeders",
                    "tracker_leechers",
                ],
                "partial": True,
                "warnings": ["Campo opcional ausente"],
            },
            scraped_at,
        )

        self.assertTrue(persisted)
        self.assertTrue(changed)
        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-202")
            ).first()
            self.assertEqual(torrent.tracker_title, "Título nuevo")
            self.assertEqual(torrent.description, "Descripción anterior")
            self.assertEqual(torrent.poster_url, "https://example.invalid/old.jpg")
            self.assertEqual(torrent.size_bytes, 1234)
            self.assertEqual(torrent.published_at, datetime(2025, 1, 2, 1, 4))
            self.assertIsNone(torrent.published_at.tzinfo)
            self.assertFalse(torrent.tracker_freeleech)
            self.assertIsNone(torrent.freeleech_until)
            self.assertEqual(torrent.tags, "[]")
            self.assertEqual(torrent.tracker_seeders, 0)
            self.assertEqual(torrent.tracker_leechers, 3)
            self.assertEqual(torrent.description_override, "Descripción manual")
            self.assertEqual(torrent.tags_override, '["Manual"]')
            self.assertEqual(torrent.peers_seeds, 99)
            self.assertEqual(torrent.peers_leechs, 88)
            self.assertIsNotNone(torrent.last_scraped_at)
            accepted_scraped_at = torrent.last_scraped_at

        persisted, changed = torrents_api._merge_rehydrate_payload(
            "UNF-202",
            {
                "structurally_valid": True,
                "scraped_from_tracker": True,
                "source_guid": "202",
                "tracker_title": "Título nuevo",
                "size_bytes": 1234,
                "freeleech_present": True,
                "freeleech_until": None,
                "observed_fields": [
                    "source_guid",
                    "tracker_title",
                    "size_bytes",
                    "freeleech_present",
                ],
            },
            scraped_at + timedelta(seconds=30),
        )
        self.assertTrue(persisted)
        self.assertTrue(changed)
        with Session(self.test_engine) as session:
            torrent = session.get(TorrentCache, "UNF-202")
            self.assertTrue(torrent.tracker_freeleech)
            self.assertIsNone(torrent.freeleech_until)
            accepted_scraped_at = torrent.last_scraped_at

        rejected = torrents_api._merge_rehydrate_payload(
            "UNF-202",
            {
                "scraped_from_tracker": True,
                "source_guid": "999",
                "tracker_title": "Ficha ajena",
                "observed_fields": ["source_guid", "tracker_title"],
            },
            scraped_at + timedelta(minutes=1),
        )
        self.assertEqual(rejected, (False, False))
        rejected_size = torrents_api._merge_rehydrate_payload(
            "UNF-202",
            {
                "structurally_valid": True,
                "scraped_from_tracker": True,
                "source_guid": "202",
                "tracker_title": "Ficha sin tamaño fiable",
                "size_bytes": 1,
                "observed_fields": ["source_guid", "tracker_title", "size_bytes"],
            },
            scraped_at + timedelta(minutes=2),
        )
        self.assertEqual(rejected_size, (False, False))
        with Session(self.test_engine) as session:
            torrent = session.exec(
                select(TorrentCache).where(TorrentCache.guid == "UNF-202")
            ).first()
            self.assertEqual(torrent.last_scraped_at, accepted_scraped_at)

    # ------------------------------------------------------------
    # Sin entradas ni salida, persiste fechas/revisiones de prueba y comprueba la
    # selección stale, orden selected y skipped_fresh. Depende de Session,
    # TorrentCache y _select_rehydrate_guids; errores DB/fecha o asserts fallan.
    # ------------------------------------------------------------
    def test_stale_selection_uses_age_and_scrape_revision(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            TorrentCache(
                guid="UNF-null",
                original_title="Null",
                enriched_title="Null",
                last_scraped_at=None,
                scrape_revision=1,
            ),
            TorrentCache(
                guid="UNF-old",
                original_title="Old",
                enriched_title="Old",
                last_scraped_at=now - timedelta(days=2),
                scrape_revision=1,
            ),
            TorrentCache(
                guid="UNF-revision",
                original_title="Revision",
                enriched_title="Revision",
                last_scraped_at=now,
                scrape_revision=0,
            ),
            TorrentCache(
                guid="UNF-fresh",
                original_title="Fresh",
                enriched_title="Fresh",
                last_scraped_at=now,
                scrape_revision=1,
            ),
        ]
        with Session(self.test_engine) as session:
            session.add_all(rows)
            session.commit()

        scope_total, eligible, skipped_fresh = torrents_api._select_rehydrate_guids(
            "selected",
            ["UNF-fresh", "UNF-missing", "UNF-old", "UNF-null", "UNF-revision"],
            "stale",
            1,
        )

        self.assertEqual(scope_total, 4)
        self.assertEqual(eligible, ["UNF-old", "UNF-null", "UNF-revision"])
        self.assertEqual(skipped_fresh, 1)

    # ------------------------------------------------------------
    # Sin entradas ni salida, persiste una ficha fresca y ejecuta el runner para
    # comprobar terminal 100 % sin red/indexador. Muta DB y slots de prueba;
    # depende de Session, asyncio y helpers de lifecycle, y errores/asserts fallan.
    # ------------------------------------------------------------
    def test_zero_eligible_job_completes_without_remote_work(self):
        with Session(self.test_engine) as session:
            session.add(TorrentCache(
                guid="UNF-fresh-only",
                original_title="Fresh only",
                enriched_title="Fresh only",
                last_scraped_at=datetime.now(timezone.utc).replace(tzinfo=None),
                scrape_revision=torrents_api.REHYDRATE_SCRAPE_REVISION,
            ))
            session.commit()

        # ------------------------------------------------------------
        # Sin entradas, crea/ejecuta el runner en el mismo loop y devuelve
        # (created, terminal). Muta slots y lee DB sin red; depende de los helpers
        # create/run y propaga errores async/DB a asyncio.run y a la prueba.
        # ------------------------------------------------------------
        async def scenario():
            job_id, created = await torrents_api._create_rehydrate_job("all", "stale", 1)
            await torrents_api._run_rehydrate_job(job_id, [])
            return created, dict(torrents_api._rehydrate_last_terminal_job)

        created, terminal = asyncio.run(scenario())

        self.assertTrue(created)
        self.assertEqual(terminal["status"], "completed")
        self.assertEqual(terminal["scope_total"], 1)
        self.assertEqual(terminal["total"], 0)
        self.assertEqual(terminal["processed"], 0)
        self.assertEqual(terminal["skipped_fresh"], 1)
        self.assertEqual(terminal["percent_total"], 100)

    # ------------------------------------------------------------
    # Sin entradas ni salida, ejecuta transiciones locales y comprueba UUID,
    # cancelación idempotente y memoria O(1). Muta/restaura slots de prueba, no
    # usa red/DB y depende de asyncio y helpers lifecycle; errores/asserts fallan.
    # ------------------------------------------------------------
    def test_single_job_lifecycle_and_idempotent_cancel(self):
        # ------------------------------------------------------------
        # Sin entradas, agrupa transiciones en un loop y devuelve un dict de
        # resultados. Muta slots y crea/espera una task pasiva sin DB/red; depende
        # de los helpers lifecycle/asyncio y propaga sus errores al test exterior.
        # ------------------------------------------------------------
        async def scenario():
            first_id, first_created = await torrents_api._create_rehydrate_job("all", "stale", 1)
            second_id, second_created = await torrents_api._create_rehydrate_job("selected", "complete", 30)
            config = await torrents_api._begin_rehydrate_job(first_id)
            first_cancel = await torrents_api.cancel_rehydrate_job(first_id)
            second_cancel = await torrents_api.cancel_rehydrate_job(first_id)
            stopped = await torrents_api._stop_rehydrate_if_requested(first_id)
            terminal_cancel = await torrents_api.cancel_rehydrate_job(first_id)
            queued_id, queued_created = await torrents_api._create_rehydrate_job("all", "complete", 1)
            passive_task = asyncio.create_task(asyncio.sleep(0))
            await torrents_api._register_rehydrate_task(queued_id, passive_task)
            task_was_registered = torrents_api._rehydrate_active_task is passive_task
            queued_cancel = await torrents_api.cancel_rehydrate_job(queued_id)
            queued_cancel_again = await torrents_api.cancel_rehydrate_job(queued_id)
            await passive_task
            task_was_cancelled = passive_task.cancelled()
            await torrents_api._release_rehydrate_task(passive_task)
            return {
                "first_id": first_id,
                "first_created": first_created,
                "second_id": second_id,
                "second_created": second_created,
                "config": config,
                "first_cancel": first_cancel,
                "second_cancel": second_cancel,
                "stopped": stopped,
                "terminal_cancel": terminal_cancel,
                "queued_created": queued_created,
                "queued_cancel": queued_cancel,
                "queued_cancel_again": queued_cancel_again,
                "task_was_registered": task_was_registered,
                "task_was_cancelled": task_was_cancelled,
            }

        result = asyncio.run(scenario())

        self.assertTrue(result["first_created"])
        self.assertEqual(result["first_id"], result["second_id"])
        self.assertFalse(result["second_created"])
        self.assertEqual(result["config"]["refresh_policy"], "stale")
        self.assertEqual(result["first_cancel"]["status"], "stopping")
        self.assertFalse(result["first_cancel"]["already_requested"])
        self.assertTrue(result["second_cancel"]["already_requested"])
        self.assertTrue(result["stopped"])
        self.assertTrue(result["terminal_cancel"]["already_cancelled"])
        self.assertTrue(result["queued_created"])
        self.assertEqual(result["queued_cancel"]["status"], "cancelled")
        self.assertTrue(result["queued_cancel_again"]["already_cancelled"])
        self.assertTrue(result["task_was_registered"])
        self.assertFalse(result["task_was_cancelled"])
        self.assertIsNone(torrents_api._rehydrate_active_task)
        self.assertIsNone(torrents_api._rehydrate_active_job)
        self.assertEqual(torrents_api._rehydrate_last_terminal_job["status"], "cancelled")
        self.assertNotIn("wanted_guids", torrents_api._rehydrate_last_terminal_job)
        self.assertNotIn("remaining_guids", torrents_api._rehydrate_last_terminal_job)

    # ------------------------------------------------------------
    # Sin entradas ni salida, simula una respuesta perdida y comprueba deduplicar
    # request_id frente a una intención nueva. Muta slots de prueba sin DB/red;
    # depende de asyncio/helpers lifecycle y errores/asserts fallan la prueba.
    # ------------------------------------------------------------
    def test_start_request_id_deduplicates_a_lost_terminal_response(self):
        # ------------------------------------------------------------
        # Sin entradas, ejecuta creación/finalización/reintentos y devuelve seis
        # valores de identidad/created. Muta slots sin DB/red; depende de helpers
        # lifecycle y propaga errores del lock/reloj al asyncio.run exterior.
        # ------------------------------------------------------------
        async def scenario():
            first_id, first_created = await torrents_api._create_rehydrate_job(
                "all", "stale", 1, request_id="request_A123"
            )
            await torrents_api._begin_rehydrate_job(first_id)
            await torrents_api._finish_rehydrate_job(first_id, "completed")
            retry_id, retry_created = await torrents_api._create_rehydrate_job(
                "all", "stale", 1, request_id="request_A123"
            )
            next_id, next_created = await torrents_api._create_rehydrate_job(
                "all", "stale", 1, request_id="request_B456"
            )
            return first_id, first_created, retry_id, retry_created, next_id, next_created

        first_id, first_created, retry_id, retry_created, next_id, next_created = asyncio.run(
            scenario()
        )

        self.assertTrue(first_created)
        self.assertEqual(retry_id, first_id)
        self.assertFalse(retry_created)
        self.assertTrue(next_created)
        self.assertNotEqual(next_id, first_id)

    # ------------------------------------------------------------
    # Sin entradas ni salida, persiste un hit sin freeleech y verifica que el DTO
    # y la DB conservan NULL. No abre red; depende de Session, TorrentCache y
    # _persist_interactive_search_results, y errores SQLite/asserts se propagan.
    # ------------------------------------------------------------
    def test_interactive_cache_hit_preserves_unknown_freeleech(self):
        with Session(self.test_engine) as session:
            session.add(TorrentCache(
                guid="UNF-404",
                source_guid="404",
                original_title="Ficha 404",
                enriched_title="Ficha 404",
                size_bytes=4096,
                tracker_freeleech=None,
                freeleech_until=None,
            ))
            session.commit()

        rendered = torrents_api._persist_interactive_search_results(
            [{
                "guid": "UNF-404",
                "source_guid": "404",
                "original_title": "Ficha 404",
                "title": "Ficha 404",
                "size_bytes": 4096,
                "scraped_from_tracker": False,
            }],
            "unionfansub",
            "Union Fansub",
            "http://testserver",
        )

        self.assertEqual(len(rendered), 1)
        self.assertFalse(rendered[0]["is_freeleech"])
        with Session(self.test_engine) as session:
            torrent = session.get(TorrentCache, "UNF-404")
            self.assertIsNone(torrent.tracker_freeleech)
            self.assertIsNone(torrent.freeleech_until)

    # ------------------------------------------------------------
    # Sin entradas ni salida, aplica un DTO qB a una ficha solo en memoria y
    # comprueba ownership del swarm. Depende de TorrentCache y
    # _apply_telemetry_to_torrent; no hace I/O y errores de conversión/asserts fallan.
    # ------------------------------------------------------------
    def test_qbittorrent_telemetry_does_not_replace_tracker_swarm(self):
        torrent = TorrentCache(
            guid="UNF-303",
            original_title="Ficha 303",
            enriched_title="Ficha 303",
            tracker_seeders=7,
            tracker_leechers=2,
        )

        torrents_api._apply_telemetry_to_torrent(torrent, {
            "peers_seeds": 50,
            "peers_leechs": 40,
            "progress": 0.5,
        })

        self.assertEqual(torrent.tracker_seeders, 7)
        self.assertEqual(torrent.tracker_leechers, 2)
        self.assertEqual(torrent.peers_seeds, 50)
        self.assertEqual(torrent.peers_leechs, 40)


if __name__ == "__main__":
    unittest.main()
