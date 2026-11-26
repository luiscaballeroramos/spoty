import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from examples.github_data_demo import publish_synthetic
from spoty.cli.operations import local_operation, main
from spoty.domain.listening import ListeningBatch, ValidationError
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoritesCoordinator
from spoty.storage.contracts import StorageError
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot


ACCOUNT = "synthetic-operations"


def listening_batch():
    artist = {"id": "R" * 22, "name": "Synthetic artist"}
    return ListeningBatch.from_items(ACCOUNT, "operations-test", "2026-11-06T10:00:00Z", [{
        "played_at": "2026-11-06T10:00:00Z", "context": None,
        "track": {
            "id": "T" * 22, "name": "Synthetic track", "duration_ms": 180000,
            "artists": [artist],
            "album": {"id": "A" * 22, "name": "Synthetic album", "artists": [artist], "total_tracks": 1},
        },
    }])


@unittest.skipUnless(shutil.which("git"), "Git is required")
class LocalOperationsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="operations-test-", dir=Path.cwd())
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tempdir = patch("tempfile.tempdir", str(self.root))
        self.tempdir.start()
        self.addCleanup(self.tempdir.stop)
        self.repository = self.root / "synthetic.git"
        self.store = LocalGitStore.initialize(self.repository)
        self.store.publish_batch(listening_batch())

    def command(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = main(list(arguments))
        return status, json.loads(output.getvalue())

    def test_status_and_audit_are_read_only_and_never_load_credentials_or_providers(self):
        head = self.store.head()
        with patch("spoty.cli.credentials.local_github_token", side_effect=AssertionError("credential read")):
            with patch("spoty.storage.github.RequestsGitHubTransport", side_effect=AssertionError("remote call")):
                with patch.object(LocalGitStore, "publish", side_effect=AssertionError("unexpected write")):
                    for operation in ("status", "audit"):
                        with self.subTest(operation=operation):
                            status, report = self.command(
                                operation, "--repository", str(self.repository), "--account", ACCOUNT,
                            )
                            self.assertEqual(status, 0)
                            self.assertTrue(report["read_only"])
                            self.assertEqual(report["head"], head)
                            self.assertEqual(report["pending_batches"], 1)
        self.assertEqual(self.store.head(), head)

    def test_audit_and_consolidation_reuse_existing_services(self):
        before = self.store.head()
        status, report = self.command(
            "consolidate", "--repository", str(self.repository), "--account", ACCOUNT,
            "--write-synthetic", "--batch-limit", "1",
        )
        self.assertEqual(status, 0)
        self.assertFalse(report["read_only"])
        self.assertEqual(report["consolidation"]["saved_events"], 1)
        self.assertEqual(report["pending_batches"], 0)
        self.assertNotEqual(report["head"], before)
        audit = local_operation("audit", self.repository, ACCOUNT)
        self.assertEqual(audit["dataset_rows"]["listening_events"], 1)
        self.assertEqual(audit["verified_captures"], 1)
        self.assertEqual(audit["archived_batches"], 1)
        self.assertFalse(audit["quarantine_contents_verified"])
        second = local_operation("consolidate", self.repository, ACCOUNT, write_synthetic=True)
        self.assertEqual(second["head"], report["head"])

    def test_consolidation_requires_explicit_write_flag(self):
        before = self.store.head()
        status, _ = self.command(
            "consolidate", "--repository", str(self.repository), "--account", ACCOUNT,
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.store.head(), before)

    def test_batch_limit_leaves_remaining_work_for_the_next_explicit_command(self):
        first = listening_batch()
        second = ListeningBatch.from_items(
            ACCOUNT, "second-producer", first.captured_at,
            [first.payloads[0]["spotify_item"]],
        )
        self.store.publish_batch(second)
        report = local_operation(
            "consolidate", self.repository, ACCOUNT, write_synthetic=True, batch_limit=1,
        )
        self.assertEqual(report["consolidation"]["accepted_batches"], 1)
        self.assertEqual(report["pending_batches"], 1)

    def test_pending_favorite_change_is_reported_without_settling_it(self):
        favorites = FavoritesCoordinator(self.store, ACCOUNT, "operations")
        favorites.begin_change("1" * 32, "T" * 22, True, "2026-11-06T10:00:00Z")
        before = self.store.head()
        for operation in ("status", "audit"):
            with self.subTest(operation=operation):
                report = local_operation(operation, self.repository, ACCOUNT)
                self.assertTrue(report["favorite_change_pending"])
        self.assertEqual(self.store.head(), before)
        self.assertIsNotNone(favorites.pending_change())

    def test_no_creation_or_production_namespaces_and_bounded_batch_limit(self):
        missing = self.root / "missing.git"
        for repository, account, limit in (
            (missing, ACCOUNT, 100), (self.root, ACCOUNT, 100),
            (self.repository, "production", 100), (self.repository, "synthetic-absent", 100),
            (self.repository, ACCOUNT, 0), (self.repository, ACCOUNT, 101),
            (self.repository, ACCOUNT, True),
        ):
            with self.subTest(repository=repository, account=account, limit=limit):
                with self.assertRaises((StorageError, ValidationError)):
                    local_operation("consolidate", repository, account, write_synthetic=True, batch_limit=limit)
        self.assertFalse(missing.exists())

    def test_mixed_repository_is_not_an_operational_target(self):
        self.store.publish(self.store.head(), {"accounts/production/README": b"not synthetic"}, "Fixture")
        before = self.store.head()
        for operation in ("status", "audit", "consolidate"):
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                local_operation(operation, self.repository, ACCOUNT, write_synthetic=True)
        self.assertEqual(self.store.head(), before)

    def test_audit_detects_corrupt_partition_without_repairing(self):
        consolidate(self.store, ACCOUNT)
        snapshot = Snapshot.load(self.store, ACCOUNT)
        part = snapshot.dataset_descriptor("listening_events")["partitions"][0]["path"]
        self.store.publish(self.store.head(), {f"accounts/{ACCOUNT}/{part}": b"corrupted"}, "Fixture")
        before = self.store.head()
        with self.assertRaises(ValidationError):
            local_operation("audit", self.repository, ACCOUNT)
        self.assertEqual(self.store.head(), before)

    def test_bad_inbox_fails_audit_and_explicit_consolidation_quarantines(self):
        path = f"accounts/{ACCOUNT}/inbox/listening_capture/2026-11/{'a' * 64}.csv"
        self.store.publish(self.store.head(), {path: b"bad envelope"}, "Fixture")
        with self.assertRaises(ValidationError):
            local_operation("audit", self.repository, ACCOUNT)
        report = local_operation("consolidate", self.repository, ACCOUNT, write_synthetic=True)
        self.assertEqual(report["consolidation"]["rejected_batches"], 1)
        self.assertEqual(self.store.read(report["head"], path.replace("/inbox/", "/quarantine/")), b"bad envelope")
        audit = local_operation("audit", self.repository, ACCOUNT)
        self.assertEqual(audit["quarantined_batches"], 1)
        self.assertFalse(audit["quarantine_contents_verified"])

    def test_missing_favorite_revision_blocks_audit_and_consolidation(self):
        favorites = FavoritesCoordinator(self.store, ACCOUNT, "operations")
        favorites.begin_change("1" * 32, "T" * 22, True, "2026-11-06T10:00:00Z")
        favorites.resolve_change(
            "1" * 32, True, "2026-11-06T10:00:00Z", resolution="confirmed", remote_settled=True,
        )
        revision = next(
            path for path in self.store.list_paths(self.store.head())
            if "/coordination/favorites/revisions/" in path
        )
        self.store.publish(self.store.head(), {revision: None}, "Fixture")
        before = self.store.head()
        for operation in ("audit", "consolidate"):
            with self.subTest(operation=operation), self.assertRaises(StorageError):
                local_operation(operation, self.repository, ACCOUNT, write_synthetic=True)
        self.assertEqual(self.store.head(), before)

    def test_synthetic_remote_demo_can_be_verified_entirely_offline(self):
        report = publish_synthetic(self.store, "synthetic-new")
        self.assertTrue(report["stale_head_rejected"])
        self.assertTrue(report["idempotent_retries"])
        audit = local_operation("audit", self.repository, "synthetic-new")
        self.assertEqual(audit["dataset_rows"]["listening_events"], 1)
        self.assertEqual(audit["dataset_rows"]["liked_tracks"], 1)
        self.assertEqual(audit["verified_captures"], 3)

    def test_cli_errors_do_not_echo_secret_paths_or_exception_details(self):
        with patch("spoty.cli.operations.local_operation", side_effect=OSError("secret-token")):
            status, report = self.command(
                "status", "--repository", "private-secret-path", "--account", ACCOUNT,
            )
        self.assertEqual(status, 1)
        self.assertNotIn("secret", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
