import builtins
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app_views.summary_source import load_local_summary_snapshot, summary_backend
from spoty.domain.listening import ValidationError, canonical_json, parse_json
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoritesCoordinator
from spoty.services.genre_taxonomy import GenreTaxonomyRepository
from spoty.storage.contracts import StorageError
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot
from tests.test_csv_git_prototype import ARTIST, batch, item
from tests.test_csv_metadata_prototype import capture, smarter


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "account-demo"


@unittest.skipUnless(shutil.which("git"), "Synthetic local Git required")
class CsvSummaryViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temporary.name) / "data.git"
        cls.store = LocalGitStore.initialize(cls.path)
        cls.store.publish_batch(batch(item()))
        cls.store.publish_batch(smarter(
            capture(entity="artist", entity_id=ARTIST, genres=["folk rock"]), live=False,
        ))
        cls.store.publish_batch(smarter(capture(
            danceability=0.5, energy=0.8, valence=0.6, tempo=120,
            acousticness=0.2, instrumentalness=0.1,
        ), live=False))
        consolidate(cls.store, ACCOUNT)
        GenreTaxonomyRepository(cls.store, ACCOUNT).import_seed({"version": 1, "genres": [
            {"id": "rock", "name": "Rock", "parents": [], "labels": ["rock"]},
            {"id": "folk", "name": "Folk", "parents": [], "labels": ["folk"]},
            {"id": "folk-rock", "name": "Folk Rock", "parents": ["rock", "folk"], "labels": ["folk rock"]},
        ]})
        cls.consolidated_commit = cls.store.head()
        cls.store.publish_batch(batch(item(played_at="2026-11-06T11:00:00Z")))
        cls.pending_commit = cls.store.head()
        cls.store.publish(cls.store.head(), {}, "Synthetic later head")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "SPOTY_SUMMARY_BACKEND": "csv_local",
            "SPOTY_CSV_REPOSITORY": str(self.path),
            "SPOTY_CSV_ACCOUNT": ACCOUNT,
            "SPOTY_CSV_COMMIT": "",
            "DATABASE_URL": "",
            "SPOTIFY_CLIENT_ID": "",
            "SPOTIFY_CLIENT_SECRET": "",
            "SPOTIFY_REFRESH_TOKEN": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def app(self):
        return AppTest.from_file(str(ROOT / "streamlit_app.py")).run(timeout=20)

    def clear_favorite_fixture(self, coordinator, request_id, liked):
        coordinator.resolve_change(
            request_id, liked, "2026-11-10T10:05:00Z",
            resolution="reconciled", remote_settled=True,
        )
        commit = self.store.head()
        paths = self.store.list_paths(commit)
        changes = {
            path: None for path in paths
            if path.startswith(f"accounts/{ACCOUNT}/coordination/favorites/")
            or path.startswith(f"accounts/{ACCOUNT}/inbox/favorites_capture/")
        }
        if changes:
            self.store.publish(commit, changes, "Remove synthetic UI test fixture")

    def test_application_renders_real_csv_counts_families_and_pending_without_writes_or_sql(self):
        before = self.store.head()
        with patch("psycopg.connect", side_effect=AssertionError("No SQL")), patch(
            "spoty.storage.local_git.LocalGitStore.publish", side_effect=AssertionError("Read-only"),
        ):
            app = self.app()
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        self.assertIn("Solo lectura", app.info[0].value)
        self.assertIn(before, app.info[0].value)
        self.assertTrue(any("Lotes pendientes: 1." in element.value for element in app.caption))
        html = "\n".join(element.value for element in app.markdown)
        self.assertEqual(html.count('class="library-stat-value">1<'), 4)
        self.assertIn(">Folk</span>", html)
        self.assertIn(">Rock</span>", html)
        self.assertEqual(self.store.head(), before)
        self.assertFalse(app.button)
        self.assertFalse(app.text_input)

    def test_pending_favorite_is_visible_read_only_and_distinct_from_inbox_batches(self):
        coordinator = FavoritesCoordinator(self.store, ACCOUNT, "synthetic-ui")
        request_id = "1" * 32
        coordinator.begin_change(
            request_id, "T" * 22, True, "2026-11-10T10:00:00Z",
        )
        self.addCleanup(self.clear_favorite_fixture, coordinator, request_id, True)
        before = self.store.head()
        with patch("psycopg.connect", side_effect=AssertionError("No SQL")), patch(
            "requests.sessions.Session.request", side_effect=AssertionError("No HTTP"),
        ), patch(
            "spoty.storage.local_git.LocalGitStore.publish",
            side_effect=AssertionError("Read-only"),
        ):
            app = self.app()

        self.assertFalse(app.exception)
        self.assertTrue(any(
            "Hay una operación pendiente para marcar como favorito" in item.value
            and "No se repetirá automáticamente" in item.value
            and "confirmada en el snapshot" in item.value
            for item in app.warning
        ))
        self.assertTrue(any(
            "Lotes pendientes: 1." in item.value for item in app.caption
        ))
        self.assertFalse(app.button)
        self.assertEqual(self.store.head(), before)

    def test_historical_snapshot_still_shows_pending_journal_from_current_head(self):
        coordinator = FavoritesCoordinator(self.store, ACCOUNT, "synthetic-ui")
        request_id = "2" * 32
        coordinator.begin_change(request_id, "T" * 22, False, "2026-11-10T10:00:00Z")
        self.addCleanup(self.clear_favorite_fixture, coordinator, request_id, False)
        current_head = self.store.head()
        with patch.dict(os.environ, {"SPOTY_CSV_COMMIT": self.consolidated_commit}):
            with patch("psycopg.connect", side_effect=AssertionError("No SQL")), patch(
                "requests.sessions.Session.request", side_effect=AssertionError("No HTTP"),
            ), patch(
                "spoty.storage.local_git.LocalGitStore.publish",
                side_effect=AssertionError("Read-only"),
            ):
                app = self.app()

        self.assertFalse(app.exception)
        self.assertIn(self.consolidated_commit, app.info[0].value)
        self.assertTrue(any("Lotes pendientes: 0." in item.value for item in app.caption))
        self.assertTrue(any(
            "Hay una operación pendiente para quitar de favoritos" in item.value
            and f"solicitud {request_id}" in item.value
            for item in app.warning
        ))
        self.assertFalse(app.button)
        self.assertEqual(self.store.head(), current_head)

    def test_genre_selector_changes_from_families_to_labels_in_csv_mode(self):
        app = self.app()
        self.assertFalse(app.exception)
        app.session_state["summary_genre_mode"] = "Subgéneros"
        app.run()
        self.assertFalse(app.exception)
        html = "\n".join(element.value for element in app.markdown)
        self.assertIn(">Folk Rock</span>", html)
        self.assertNotIn(">Folk</span>", html)

    def test_metadata_page_uses_pinned_csv_snapshot_without_sql_or_spotify(self):
        original_import = builtins.__import__
        forbidden = {
            "services.summary_queries", "services.synchronization", "spotifyapi.errors",
            "_registration", "config", "spotifyapi.streamlit_auth",
        }
        attempted = []

        def guarded_import(name, *args, **kwargs):
            if name in forbidden:
                attempted.append(name)
                raise AssertionError(f"CSV metadata must not import {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import), patch(
            "psycopg.connect", side_effect=AssertionError("No SQL"),
        ), patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
            app = self.app()
            self.assertFalse(app.exception)
            app.radio(key="csv_read_only_page").set_value("Metadatos").run()
            app.selectbox(key="metadata_entity_type").select("artists").run()

        self.assertFalse(app.exception)
        self.assertEqual(attempted, [])
        self.assertIn(self.store.head(), app.info[0].value)
        self.assertTrue(any("Lotes pendientes: 1." in element.value for element in app.caption))
        self.assertEqual([metric.value for metric in app.metric], ["1", "1", "100.0%"])
        self.assertEqual(len(app.get("vega_lite_chart")), 2)

    def test_pulse_page_renders_historical_csv_without_sql_spotify_or_writes(self):
        original_import = builtins.__import__
        forbidden = {
            "services.summary_queries", "services.synchronization", "spotifyapi.errors",
            "_registration", "config", "spotifyapi.streamlit_auth", "app_views.reproduction",
            "metadata_sources.smarterplaylists",
        }
        attempted = []

        def guarded_import(name, *args, **kwargs):
            if name in forbidden:
                attempted.append(name)
                raise AssertionError(f"CSV Pulse must not import {name}")
            return original_import(name, *args, **kwargs)

        before = self.store.head()
        with patch("builtins.__import__", side_effect=guarded_import), patch(
            "psycopg.connect", side_effect=AssertionError("No SQL"),
        ), patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")), patch(
            "spoty.storage.local_git.LocalGitStore.publish", side_effect=AssertionError("Read-only"),
        ):
            app = self.app()
            self.assertFalse(app.exception)
            app.radio(key="csv_read_only_page").set_value("Pulse").run()
            app.session_state["csv_pulse_hours"] = "∞"
            app.run()

        self.assertFalse(app.exception)
        self.assertEqual(attempted, [])
        self.assertIn(before, app.info[0].value)
        self.assertTrue(any("Lotes pendientes: 1." in element.value for element in app.caption))
        self.assertTrue(any("1 escucha" in element.value for element in app.markdown))
        self.assertTrue(any("Perfil sonoro reciente" in element.value for element in app.markdown))
        self.assertEqual(self.store.head(), before)

    def test_listening_history_is_snapshot_only_and_disables_live_reproduction(self):
        original_import = builtins.__import__
        forbidden = {
            "services.summary_queries", "services.synchronization", "spotifyapi.errors",
            "_registration", "config", "spotifyapi.streamlit_auth", "app_views.reproduction",
            "spotifyapi.spotifyclient", "metadata_sources.smarterplaylists",
        }
        attempted = []

        def guarded_import(name, *args, **kwargs):
            if name in forbidden:
                attempted.append(name)
                raise AssertionError(f"CSV listening history must not import {name}")
            return original_import(name, *args, **kwargs)

        before = self.store.head()
        with patch("builtins.__import__", side_effect=guarded_import), patch(
            "psycopg.connect", side_effect=AssertionError("No SQL"),
        ), patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")), patch(
            "spoty.storage.local_git.LocalGitStore.publish", side_effect=AssertionError("Read-only"),
        ):
            app = self.app()
            self.assertFalse(app.exception)
            app.radio(key="csv_read_only_page").set_value("Historial").run(timeout=20)

        self.assertFalse(app.exception)
        self.assertEqual(attempted, [])
        self.assertIn(before, app.info[0].value)
        self.assertTrue(any("Lotes pendientes: 1." in element.value for element in app.caption))
        self.assertIn("La reproducción y los cambios de favoritos requieren el modo SQL/live.",
                      [element.value for element in app.caption])
        self.assertEqual(len(app.dataframe), 1)
        table = app.dataframe[0].value
        self.assertEqual(table.iloc[0]["Canción"], 'Cancion, "uno"\nsegunda linea')
        self.assertEqual(table.iloc[0]["Artistas"], "Artista")
        self.assertEqual(table.iloc[0]["Fecha (UTC)"], "2026-11-06 10:00:00")
        self.assertEqual(self.store.head(), before)

    def test_csv_entrypoint_skips_production_config_oauth_navigation_and_capture_imports(self):
        original_import = builtins.__import__
        forbidden = {
            "config", "app_views.metadata", "app_views.pulse", "app_views.listening_event_capture",
            "app_views.reproduction", "app_views.top_navigation", "spotifyapi.streamlit_auth",
            "services.summary_queries", "services.synchronization", "spotifyapi.errors", "_registration",
        }
        attempted = []

        def guarded_import(name, *args, **kwargs):
            if name in forbidden:
                attempted.append(name)
                raise AssertionError(f"CSV mode must not import {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import), patch(
            "requests.sessions.Session.request", side_effect=AssertionError("No HTTP"),
        ), patch("urllib.request.urlopen", side_effect=AssertionError("No HTTP")):
            app = self.app()
        self.assertFalse(app.exception)
        self.assertEqual(attempted, [])
        self.assertTrue(app.info)

    def test_explicit_commit_pins_counts_and_pending_from_same_revision(self):
        with patch.dict(os.environ, {"SPOTY_CSV_COMMIT": self.consolidated_commit}):
            app = self.app()
            snapshot = load_local_summary_snapshot()
        self.assertFalse(app.exception)
        self.assertIn(self.consolidated_commit, app.info[0].value)
        self.assertTrue(any("Lotes pendientes: 0." in element.value for element in app.caption))
        self.assertEqual(snapshot.commit_id, self.consolidated_commit)
        self.assertIsInstance(snapshot.store, LocalGitStore)
        if isinstance(snapshot.store, LocalGitStore):
            self.assertTrue(snapshot.store.read_only)

    def test_read_only_store_rejects_all_publication_paths(self):
        store = LocalGitStore(self.path, read_only=True)
        before = store.head()
        with self.assertRaisesRegex(StorageError, "read-only"):
            store.publish(before, {}, "Forbidden")
        with self.assertRaisesRegex(StorageError, "read-only"):
            store.publish_batch(batch(item(played_at="2026-11-06T12:00:00Z")))
        with self.assertRaisesRegex(StorageError, "read-only"):
            store._git("fetch", "origin")
        self.assertEqual(store.head(), before)

    def test_invalid_configuration_reports_notice_without_sql_fallback(self):
        cases = [
            {"SPOTY_SUMMARY_BACKEND": "bad"},
            {"SPOTY_CSV_REPOSITORY": ""},
            {"SPOTY_CSV_REPOSITORY": "relative.git"},
            {"SPOTY_CSV_REPOSITORY": str(self.path.parent / "missing.git")},
            {"SPOTY_CSV_REPOSITORY": str(ROOT)},
            {"SPOTY_CSV_ACCOUNT": "bad/account"},
            {"SPOTY_CSV_ACCOUNT": "missing-account"},
            {"SPOTY_CSV_COMMIT": "main"},
            {"SPOTY_CSV_COMMIT": "f" * 40},
        ]
        original_import = builtins.__import__
        forbidden = {"services.summary_queries", "services.synchronization", "spotifyapi.errors", "_registration"}
        attempted = []

        def guarded_import(name, *args, **kwargs):
            if name in forbidden:
                attempted.append(name)
                raise AssertionError(f"CSV mode must not import {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            for case in cases:
                with self.subTest(case=case), patch.dict(os.environ, case):
                    app = self.app()
                    self.assertFalse(app.exception)
                    self.assertTrue(any("No se pudo cargar" in element.value for element in app.warning))
                    self.assertFalse(any("library-infographic" in element.value for element in app.markdown))
        self.assertEqual(attempted, [])

    def test_source_configuration_fails_explicitly_before_accessing_wrong_paths(self):
        for case in (
            {"SPOTY_CSV_REPOSITORY": ""}, {"SPOTY_CSV_ACCOUNT": "bad/account"},
            {"SPOTY_CSV_COMMIT": "main"},
        ):
            with self.subTest(case=case), patch.dict(os.environ, case):
                with self.assertRaises(ValidationError):
                    load_local_summary_snapshot()

    def test_default_backend_remains_sql_and_unknown_values_fail(self):
        with patch.dict(os.environ, {"SPOTY_SUMMARY_BACKEND": "sql"}):
            self.assertEqual(summary_backend(), "sql")
        with patch.dict(os.environ):
            os.environ.pop("SPOTY_SUMMARY_BACKEND")
            self.assertEqual(summary_backend(), "sql")
        with patch.dict(os.environ, {"SPOTY_SUMMARY_BACKEND": "github"}):
            with self.assertRaises(ValidationError):
                summary_backend()

    def test_snapshot_without_taxonomy_reports_genre_error_but_allows_labels(self):
        source = Snapshot.load(self.store, ACCOUNT, self.consolidated_commit)
        path = f"accounts/{ACCOUNT}/manifest.json"
        manifest = parse_json(self.store.read(source.commit_id, path).decode())
        manifest["schema_version"] = 3
        manifest["datasets"] = {key: value for key, value in manifest["datasets"].items()
                                if not key.startswith("genre") and key != "metadata_coverage"}
        with tempfile.TemporaryDirectory() as directory:
            store = LocalGitStore.initialize(Path(directory) / "old.git")
            files = {path: self.store.read(source.commit_id, path)
                     for path in self.store.list_paths(source.commit_id)}
            files[path] = canonical_json(manifest).encode()
            store.publish(store.head(), files, "Synthetic v3 snapshot")
            with patch.dict(os.environ, {"SPOTY_CSV_REPOSITORY": str(store.path)}):
                app = self.app()
                self.assertFalse(app.exception)
                self.assertTrue(any("No se pudo calcular" in element.value for element in app.warning))
                app.session_state["summary_genre_mode"] = "Subgéneros"
                app.run()
                self.assertFalse(app.exception)
                self.assertFalse(app.warning)
                self.assertIn(">Folk Rock</span>", "\n".join(element.value for element in app.markdown))

    def test_corrupt_partition_reports_error_without_using_sql(self):
        source = Snapshot.load(self.store, ACCOUNT)
        path = next(path for path in self.store.list_paths(source.commit_id) if "/datasets/tracks/" in path)
        original_read = LocalGitStore.read
        original_read_many = LocalGitStore.read_many
        with patch("spoty.storage.local_git.LocalGitStore.read", autospec=True) as read:
            read.side_effect = lambda store, commit, file: b"bad" if file == path else original_read(store, commit, file)
            with patch("spoty.storage.local_git.LocalGitStore.read_many", autospec=True) as read_many:
                read_many.side_effect = lambda store, commit, paths: [
                    b"bad" if file == path else data
                    for file, data in zip(paths, original_read_many(store, commit, paths))
                ]
                app = self.app()
        self.assertFalse(app.exception)
        self.assertTrue(any("No se pudo cargar" in element.value for element in app.warning))
