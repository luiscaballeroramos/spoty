import gc
import hashlib
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from spoty.domain.listening import ValidationError, canonical_json
from spoty.storage.csv_codec import SCHEMAS, decode_csv
from spoty.storage.github import GitHubGitStore
from spoty.storage.local_git import LocalGitStore
from spoty.storage.staging import DiskRows, FileChanges, staging_database
from tests.test_github_data_store import FakeGitHub


class WindowedChanges(Mapping):
    class Content(bytes):
        live = 0
        peak = 0

        def __new__(cls, data):
            cls.live += 1
            cls.peak = max(cls.peak, cls.live)
            if cls.live > 3:
                cls.live -= 1
                raise AssertionError("Publication retained all change values.")
            return super().__new__(cls, data)

        def __del__(self):
            type(self).live -= 1

    def __iter__(self):
        return iter(f"data-{index:02d}.csv" for index in range(20))

    def __len__(self):
        return 20

    def __getitem__(self, path):
        if path not in list(self):
            raise KeyError(path)
        return self.Content(path.encode())


class CsvStagingTest(unittest.TestCase):
    def test_file_changes_keep_values_on_disk_and_handle_replacement_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            changes = FileChanges(Path(temporary) / "changes")
            changes["data.csv"] = b"first"
            previous = changes.files["data.csv"]
            self.assertEqual(changes["data.csv"], b"first")
            self.assertEqual(len(changes), 1)
            changes["data.csv"] = b"second"
            self.assertFalse(previous.exists())
            changes["deleted.csv"] = None
            self.assertEqual(dict(changes), {"data.csv": b"second", "deleted.csv": None})
            previous = changes.files["data.csv"]
            del changes["data.csv"]
            self.assertFalse(previous.exists())
            with self.assertRaises(KeyError):
                changes["data.csv"]
            with self.assertRaises(ValidationError):
                changes["../escape"] = b"invalid"
            with self.assertRaises(ValidationError):
                changes["invalid.csv"] = "not bytes"

    def test_disk_rows_validate_duplicates_and_publish_sorted_bounded_partitions(self):
        with tempfile.TemporaryDirectory() as temporary, \
                closing(staging_database(Path(temporary) / "rows.sqlite")) as database:
            disk = DiskRows(database, "lastfm_track_metadata")
            rows = [{
                "spotify_track_id": f"T{index:021d}", "match_status": "matched",
                "normalized_data": {"name": "Name", "zero": 0}, "raw_response": {"text": "x" * 100},
                "fetched_at": "2026-11-09T12:00:00Z",
            } for index in range(40)]
            for row in reversed(rows):
                disk.put(row)
            self.assertEqual(disk.get((rows[0]["spotify_track_id"],)), rows[0])
            self.assertIsNone(disk.get(("missing",)))
            with self.assertRaisesRegex(ValidationError, "Duplicate"):
                disk.put(rows[0])
            updated = {**rows[0], "normalized_data": {"zero": False}}
            disk.put(updated, replace=True)
            rows[0] = updated
            changes = FileChanges(Path(temporary) / "changes")
            with patch("spoty.storage.staging.MAX_CSV_BYTES", 800):
                entry = disk.publish_parts(changes, "synthetic-test")
            decoded = []
            for part in entry["partitions"]:
                data = changes[f"accounts/synthetic-test/{part['path']}"]
                self.assertEqual(hashlib.sha256(data).hexdigest(), part["sha256"])
                self.assertLessEqual(len(data), 800)
                self.assertEqual(len(data), part["byte_size"])
                records = decode_csv(SCHEMAS[disk.dataset], data)
                self.assertEqual(len(records), part["row_count"])
                self.assertEqual(records, sorted(records, key=lambda row: row["spotify_track_id"]))
                for row in records:
                    bucket = hashlib.sha256(canonical_json((row["spotify_track_id"],)).encode()).digest()[0] % 16
                    self.assertIn(f"/bucket-{bucket:02d}/", part["path"])
                decoded.extend(records)
            self.assertEqual(sorted(decoded, key=lambda row: row["spotify_track_id"]), rows)
            self.assertGreater(len(entry["partitions"]), 16)

    def test_empty_and_oversized_disk_dataset_publications(self):
        with tempfile.TemporaryDirectory() as temporary, \
                closing(staging_database(Path(temporary) / "rows.sqlite")) as database:
            changes = FileChanges(Path(temporary) / "changes")
            disk = DiskRows(database, "lastfm_track_metadata")
            entry = disk.publish_parts(changes, "synthetic-test")
            self.assertEqual(entry["partitions"][0]["row_count"], 0)
            self.assertEqual(len(entry["partitions"]), 1)
            disk.put({
                "spotify_track_id": "T" * 22, "match_status": "matched", "normalized_data": {},
                "raw_response": {"data": "x" * 1000}, "fetched_at": "date",
            })
            with patch("spoty.storage.staging.MAX_CSV_BYTES", 800), self.assertRaisesRegex(ValidationError, "row exceeds"):
                disk.publish_parts(changes, "synthetic-test")

    def test_github_publication_does_not_materialize_lazy_change_values(self):
        gc.collect()
        WindowedChanges.Content.peak = 0
        fake = FakeGitHub()
        store = GitHubGitStore("demo", "private-data", fake)
        commit = store.publish(store.head(), WindowedChanges(), "Synthetic lazy publication")
        self.assertEqual(len(store.list_paths(commit)), 20)
        self.assertLessEqual(WindowedChanges.Content.peak, 3)

    @unittest.skipUnless(shutil.which("git"), "Git required")
    def test_local_publication_does_not_materialize_lazy_change_values(self):
        gc.collect()
        WindowedChanges.Content.peak = 0
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalGitStore.initialize(Path(temporary) / "data.git")
            commit = store.publish(store.head(), WindowedChanges(), "Synthetic lazy publication")
            self.assertEqual(len(store.list_paths(commit)), 20)
        self.assertLessEqual(WindowedChanges.Content.peak, 3)
