import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace
from unittest.mock import patch

from app_views.summary_source import (
    SYNTHETIC_WRITE_OPT_IN, load_synthetic_writable_store,
)
from app_views.csv_favorite_status import render_csv_favorite_status
from spoty.domain.listening import ValidationError
from spoty.services.acquisition import CsvAcquisitionService
from spoty.services.favorite_recovery import (
    AuthenticatedSpotifyFavoriteRecovery, _authenticated_spotify_client,
)
from spoty.services.favorites import FavoritesCoordinator
from spoty.storage.contracts import StorageError
from spoty.storage.csv_codec import decode_batch
from spoty.storage.local_git import LocalGitStore, SyntheticLocalGitStore


TRACK = "T" * 22
REQUEST_ID = "1" * 32
STARTED_AT = "2026-11-10T10:00:00Z"
OBSERVED_AT = "2026-11-10T10:05:00Z"


class AmbiguousFavoriteProvider:
    def __init__(self):
        self.liked = set()
        self.mutations = []
        self.observations = []

    def like_track(self, track_id):
        self.mutations.append(("like", track_id))
        self.liked.add(track_id)
        raise TimeoutError("Synthetic response lost after mutation.")

    def unlike_track(self, track_id):
        self.mutations.append(("unlike", track_id))
        self.liked.discard(track_id)
        raise TimeoutError("Synthetic response lost after mutation.")

    def are_tracks_liked(self, track_ids):
        self.observations.append(list(track_ids))
        return {track_id: track_id in self.liked for track_id in track_ids}


class ObservingFavoriteProvider:
    def __init__(self):
        self.observations = []
        self.mutations = []

    def are_tracks_liked(self, track_ids):
        self.observations.append(list(track_ids))
        return {track_id: True for track_id in track_ids}

    def like_track(self, track_id):
        self.mutations.append(("like", track_id))
        raise AssertionError("Recovery must not replay a like.")

    def unlike_track(self, track_id):
        self.mutations.append(("unlike", track_id))
        raise AssertionError("Recovery must not replay an unlike.")


@unittest.skipUnless(shutil.which("git"), "Synthetic local Git required")
class SyntheticWriterBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repository = root / "synthetic.git"
        self.store = LocalGitStore.initialize(self.repository)
        self.store.publish(self.store.head(), {
            "accounts/synthetic-demo/catalog.csv": b"id\n",
        }, "Create synthetic account fixture")
        self.environment = patch.dict(os.environ, {
            SYNTHETIC_WRITE_OPT_IN: "1",
            "SPOTY_CSV_REPOSITORY": str(self.repository),
            "SPOTY_CSV_ACCOUNT": "synthetic-demo",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_writer_requires_explicit_opt_in(self):
        with patch.dict(os.environ):
            os.environ.pop(SYNTHETIC_WRITE_OPT_IN)
            with self.assertRaises(ValidationError):
                load_synthetic_writable_store()

    def test_explicit_writer_is_local_and_can_journal_without_provider_calls(self):
        before = self.store.head()
        writable = load_synthetic_writable_store()
        self.assertFalse(writable.read_only)
        self.assertIsInstance(writable, SyntheticLocalGitStore)
        with self.assertRaises(StorageError):
            writable.publish(writable.head(), {
                "accounts/production/README": b"forbidden\n",
            }, "Reject cross-account write")
        reservation = FavoritesCoordinator(
            writable, "synthetic-demo", "synthetic-ui",
        ).begin_change("1" * 32, "T" * 22, True, "2026-11-10T10:00:00Z")
        self.assertEqual(reservation.status, "reserved")
        self.assertNotEqual(writable.head(), before)
        self.assertEqual(
            FavoritesCoordinator(writable, "synthetic-demo", "reader").pending_change()["request_id"],
            "1" * 32,
        )

    def test_writer_rejects_non_synthetic_account_and_mixed_repository(self):
        with patch.dict(os.environ, {"SPOTY_CSV_ACCOUNT": "production"}):
            with self.assertRaises(ValidationError):
                load_synthetic_writable_store()

        mixed = LocalGitStore.initialize(Path(self.temporary.name) / "mixed.git")
        mixed.publish(mixed.head(), {
            "accounts/synthetic-demo/catalog.csv": b"id\n",
            "accounts/production/catalog.csv": b"id\n",
        }, "Create mixed fixture")
        with patch.dict(os.environ, {"SPOTY_CSV_REPOSITORY": str(mixed.path)}):
            with self.assertRaises(StorageError):
                load_synthetic_writable_store()

    def test_writer_rejects_repository_with_remote(self):
        self.store._git("remote", "add", "origin", "https://example.invalid/data.git")
        with self.assertRaises(StorageError):
            SyntheticLocalGitStore(self.repository, "synthetic-demo")

    def test_direct_writer_constructor_also_requires_opt_in(self):
        with patch.dict(os.environ):
            os.environ.pop(SYNTHETIC_WRITE_OPT_IN)
            with self.assertRaises(ValidationError):
                SyntheticLocalGitStore(self.repository, "synthetic-demo")

    def test_favorite_recovery_survives_reopen_without_replaying_mutation(self):
        provider = AmbiguousFavoriteProvider()
        first_store = load_synthetic_writable_store()
        first_service = CsvAcquisitionService(
            first_store, "synthetic-demo", "synthetic-integration", spotify=provider,
        )
        with self.assertRaises(TimeoutError):
            first_service.change_favorite(
                REQUEST_ID, TRACK, True, STARTED_AT, lambda: OBSERVED_AT,
            )
        self.assertEqual(provider.mutations, [("like", TRACK)])

        reopened_store = load_synthetic_writable_store()
        reopened_service = CsvAcquisitionService(
            reopened_store, "synthetic-demo", "synthetic-recovery", spotify=provider,
        )
        pending = reopened_service.favorites.pending_change()
        self.assertEqual(pending["request_id"], REQUEST_ID)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(
            reopened_service.change_favorite(
                REQUEST_ID, TRACK, True, STARTED_AT, lambda: OBSERVED_AT,
            ).status,
            "already_pending",
        )
        self.assertEqual(provider.mutations, [("like", TRACK)])
        self.assertFalse(provider.observations)

        with self.assertRaises(ValidationError):
            reopened_service.recover_pending_favorite(lambda: OBSERVED_AT)
        self.assertFalse(provider.observations)
        self.assertEqual(
            reopened_service.recover_pending_favorite(
                lambda: OBSERVED_AT, remote_settled=True,
            ).status,
            "saved",
        )

        self.assertEqual(provider.observations, [[TRACK]])
        self.assertEqual(provider.mutations, [("like", TRACK)])
        self.assertIsNone(reopened_service.favorites.pending_change())
        inbox_paths = [
            path for path in reopened_store.list_paths(reopened_store.head())
            if path.startswith("accounts/synthetic-demo/inbox/favorites_capture/")
        ]
        self.assertEqual(len(inbox_paths), 1)
        capture = decode_batch(reopened_store.read(reopened_store.head(), inbox_paths[0]))
        self.assertEqual(capture.payloads[0]["request_id"], REQUEST_ID)
        self.assertTrue(capture.payloads[0]["desired_liked"])
        self.assertTrue(capture.payloads[0]["liked"])
        self.assertEqual(capture.payloads[0]["resolution"], "reconciled")

    def test_recovery_ui_requires_settlement_confirmation_and_only_observes(self):
        request_id = "3" * 32
        writer = load_synthetic_writable_store()
        coordinator = FavoritesCoordinator(
            writer, "synthetic-demo", "synthetic-ui",
        )
        coordinator.begin_change(request_id, TRACK, True, STARTED_AT)
        snapshot = SimpleNamespace(
            store=LocalGitStore(self.repository, read_only=True),
            account="synthetic-demo",
        )
        provider = ObservingFavoriteProvider()
        factory_calls = []

        def client_factory():
            factory_calls.append(True)
            return provider

        recover = AuthenticatedSpotifyFavoriteRecovery(
            writer,
            "synthetic-demo",
            "synthetic-recovery-ui",
            client_factory=client_factory,
            observed_at=lambda: OBSERVED_AT,
        )

        with patch("app_views.csv_favorite_status.st.warning"), patch(
            "app_views.csv_favorite_status.st.checkbox", return_value=False,
        ), patch(
            "app_views.csv_favorite_status.st.button", return_value=False,
        ) as button, patch("app_views.csv_favorite_status.st.caption"):
            render_csv_favorite_status(snapshot, recover_pending=recover)
        self.assertTrue(button.call_args.kwargs["disabled"])
        self.assertFalse(provider.observations)
        self.assertFalse(provider.mutations)
        self.assertIsNotNone(coordinator.pending_change())
        self.assertFalse(factory_calls)

        with patch("app_views.csv_favorite_status.st.warning"), patch(
            "app_views.csv_favorite_status.st.checkbox", return_value=True,
        ), patch(
            "app_views.csv_favorite_status.st.button", return_value=True,
        ) as button, patch(
            "app_views.csv_favorite_status.st.success",
        ) as success, patch("app_views.csv_favorite_status.clear_incident"):
            render_csv_favorite_status(snapshot, recover_pending=recover)

        self.assertFalse(button.call_args.kwargs["disabled"])
        self.assertEqual(provider.observations, [[TRACK]])
        self.assertEqual(factory_calls, [True])
        self.assertFalse(provider.mutations)
        self.assertIsNone(coordinator.pending_change())
        self.assertIn("inbox", success.call_args.args[0])
        paths = [
            path for path in writer.list_paths(writer.head())
            if path.startswith("accounts/synthetic-demo/inbox/favorites_capture/")
        ]
        self.assertEqual(len(paths), 1)
        capture = decode_batch(writer.read(writer.head(), paths[0]))
        self.assertEqual(capture.payloads[0]["resolution"], "reconciled")

    def test_authenticated_recovery_rejects_stale_request_before_authentication(self):
        request_id = "5" * 32
        writer = load_synthetic_writable_store()
        FavoritesCoordinator(
            writer, "synthetic-demo", "synthetic-ui",
        ).begin_change(request_id, TRACK, True, STARTED_AT)
        factory_calls = []

        def client_factory():
            factory_calls.append(True)
            return ObservingFavoriteProvider()

        recovery = AuthenticatedSpotifyFavoriteRecovery(
            writer,
            "synthetic-demo",
            "synthetic-recovery-ui",
            client_factory=client_factory,
            observed_at=lambda: OBSERVED_AT,
        )
        with self.assertRaises(StorageError):
            recovery.recover("6" * 32, remote_settled=True)
        with self.assertRaises(ValidationError):
            recovery.recover(request_id, remote_settled=False)
        self.assertFalse(factory_calls)

    def test_default_authenticated_client_loads_only_spotify_secrets_on_demand(self):
        names = (
            "SPOTIFY_CLIENT_ID",
            "SPOTIFY_CLIENT_SECRET",
            "SPOTIFY_REDIRECT_URI",
            "SPOTIFY_REFRESH_TOKEN",
        )
        fake_secrets = {
            "SPOTIFY_CLIENT_ID": "synthetic-client-id",
            "SPOTIFY_CLIENT_SECRET": "synthetic-client-secret",
            "SPOTIFY_REDIRECT_URI": "http://localhost/callback",
            "SPOTIFY_REFRESH_TOKEN": "synthetic-refresh-token",
            "DATABASE_URL": "must-not-be-loaded",
        }
        client = SimpleNamespace(
            are_tracks_liked=lambda track_ids: {track_id: True for track_id in track_ids},
        )
        fake_spotify_module = SimpleNamespace(
            SpotifyClient=lambda: client,
        )
        fake_streamlit = SimpleNamespace(
            secrets=fake_secrets,
            errors=SimpleNamespace(
                StreamlitSecretNotFoundError=type(
                    "StreamlitSecretNotFoundError", (Exception,), {},
                ),
            ),
        )
        with patch.dict(os.environ):
            for name in names:
                os.environ.pop(name, None)
            os.environ.pop("DATABASE_URL", None)
            with patch.dict(sys.modules, {
                "streamlit": fake_streamlit,
                "streamlit.errors": fake_streamlit.errors,
                "spotifyapi.spotifyclient": fake_spotify_module,
            }):
                result = _authenticated_spotify_client()
                loaded_values = {name: os.environ[name] for name in names}
                database_url_loaded = "DATABASE_URL" in os.environ

        self.assertEqual(result.are_tracks_liked([TRACK]), {TRACK: True})
        self.assertEqual(loaded_values, {name: fake_secrets[name] for name in names})
        self.assertFalse(database_url_loaded)

    def test_spotify_client_import_does_not_load_legacy_registration_database(self):
        script = """
import builtins
import importlib

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.startswith("register"):
        raise AssertionError("Spotify favorite recovery imported legacy registration")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
importlib.import_module("spotifyapi.spotifyclient")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_ui_keeps_journal_pending_when_provider_observation_fails(self):
        request_id = "4" * 32
        writer = load_synthetic_writable_store()
        coordinator = FavoritesCoordinator(
            writer, "synthetic-demo", "synthetic-ui",
        )
        coordinator.begin_change(request_id, TRACK, True, STARTED_AT)
        snapshot = SimpleNamespace(
            store=LocalGitStore(self.repository, read_only=True),
            account="synthetic-demo",
        )
        provider_error = TimeoutError("Synthetic observation timeout.")

        def fail_recovery(expected_request_id, *, remote_settled):
            self.assertEqual(expected_request_id, request_id)
            self.assertTrue(remote_settled)
            raise provider_error

        with patch("app_views.csv_favorite_status.st.warning"), patch(
            "app_views.csv_favorite_status.st.checkbox", return_value=True,
        ), patch(
            "app_views.csv_favorite_status.st.button", return_value=True,
        ), patch(
            "app_views.csv_favorite_status.render_incident_notice",
        ) as notice, patch("app_views.csv_favorite_status.st.success") as success:
            render_csv_favorite_status(snapshot, recover_pending=fail_recovery)

        self.assertIs(notice.call_args.args[1], provider_error)
        self.assertFalse(success.called)
        self.assertEqual(coordinator.pending_change()["request_id"], request_id)
