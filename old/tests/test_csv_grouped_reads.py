import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples.benchmark_csv import Profile, synthetic_rows
from spoty.domain.listening import ValidationError
from spoty.services.summary_queries import SummaryQueryService
from spoty.storage.contracts import GroupedReadStore, StorageError
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot, snapshot_changes
from tests.test_csv_summary_queries import MemoryStore


ACCOUNT = "synthetic-benchmark"


@unittest.skipUnless(shutil.which("git"), "Local synthetic Git required")
class LocalGroupedReadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.store = LocalGitStore.initialize(Path(cls.temporary.name) / "data.git")
        base = Snapshot.load(cls.store, ACCOUNT)
        rows = synthetic_rows(Profile(tracks=48, events=70, likes=12, incremental_events=2))
        changes = snapshot_changes(base, rows, snapshot_id="a" * 32, published_at="2026-11-09T10:00:00Z")
        changes.update({"binary/blob": b"\x00\n\xff\r\n", "binary/empty": b""})
        cls.commit = cls.store.publish(base.commit_id, changes, "Synthetic grouped read data")
        cls.snapshot = Snapshot.load(cls.store, ACCOUNT)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_multi_blob_exact_bytes_order_duplicates_and_single_process(self):
        paths = ["binary/blob", "binary/empty", "binary/blob"]
        with patch.object(self.store, "_git", wraps=self.store._git) as git:
            result = self.store.read_many(self.commit, paths)
        self.assertEqual(result, [b"\x00\n\xff\r\n", b"", b"\x00\n\xff\r\n"])
        git.assert_called_once_with("cat-file", "--batch",
                                    data="".join(f"{self.commit}:{path}\n" for path in paths).encode("ascii"))

    def test_empty_batch_validates_commit_but_starts_no_process(self):
        with patch.object(self.store, "_git", side_effect=AssertionError("No subprocess")):
            self.assertEqual(self.store.read_many(self.commit, []), [])
            with self.assertRaises(ValidationError):
                self.store.read_many("main", [])

    def test_invalid_inputs_rejected_before_git(self):
        with patch.object(self.store, "_git", side_effect=AssertionError("No subprocess")):
            for paths in (["../secret"], ["binary/blob\nHEAD"], ["binary:blob"], None, ("binary/blob",)):
                with self.subTest(paths=paths), self.assertRaises(ValidationError):
                    self.store.read_many(self.commit, paths)

    def test_missing_blob_or_tree_fails_instead_of_partial_results(self):
        for path in ("missing.csv", "binary"):
            with self.subTest(path=path), self.assertRaises(StorageError):
                self.store.read_many(self.commit, ["binary/blob", path])

    def test_protocol_errors_fail_without_single_read_fallback(self):
        header = b"a" * 40 + b" blob "
        for output in (
            b"", b"bad\n", header + b"-1\n", header + b"2\nx\n",
            header + b"0\n", header + b"0\n\nextra", b"a" * 40 + b" tree 0\n\n",
            header + b"1\nxy",
        ):
            with self.subTest(output=output), patch.object(self.store, "_git", return_value=output):
                with self.assertRaises(StorageError):
                    self.store.read_many(self.commit, ["binary/blob"])
        with patch.object(self.store, "_git", side_effect=StorageError("Git unavailable")):
            with self.assertRaisesRegex(StorageError, "unavailable"):
                self.store.read_many(self.commit, ["binary/blob"])

    def test_read_only_store_supports_grouped_reads_not_other_cat_file_commands(self):
        store = LocalGitStore(self.store.path, read_only=True)
        head = store.head()
        self.assertEqual(store.read_many(self.commit, ["binary/blob"]), [b"\x00\n\xff\r\n"])
        for arguments in (("cat-file", "-p", self.commit), ("cat-file", "--batch", "--filters"), ("fetch", "origin")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(StorageError, "read-only"):
                store._git(*arguments)
        self.assertEqual(store.head(), head)

    def test_dataset_uses_one_process_and_matches_single_read_store(self):
        memory = MemoryStore()
        memory.commits[self.commit] = {
            path: self.store.read(self.commit, path) for path in self.store.list_paths(self.commit)
        }
        single = Snapshot.load(memory, ACCOUNT, self.commit)
        self.assertFalse(isinstance(memory, GroupedReadStore))
        with patch.object(self.store, "_git", wraps=self.store._git) as git:
            grouped = self.snapshot.rows("tracks")
        self.assertGreater(len(self.snapshot.manifest["datasets"]["tracks"]["partitions"]), 1)
        self.assertEqual(git.call_count, 1)
        self.assertEqual(grouped, single.rows("tracks"))
        with patch.object(self.store, "_git", wraps=self.store._git) as git:
            counts = SummaryQueryService(self.snapshot).library_counts()
        self.assertEqual(git.call_count, 5)
        self.assertEqual(counts, SummaryQueryService(single).library_counts())

    def test_grouped_query_stays_pinned_after_head_changes(self):
        old = self.store.read(self.commit, "binary/blob")
        current = self.store.publish(self.store.head(), {"binary/blob": b"new"}, "Synthetic newer blob")
        self.assertEqual(self.store.read_many(self.commit, ["binary/blob"]), [old])
        self.assertEqual(self.store.read_many(current, ["binary/blob"]), [b"new"])


class GroupedMemoryStore(MemoryStore):
    def read_many(self, commit, paths):
        return [self.read(commit, path) for path in paths]


class GroupedSnapshotIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.store = GroupedMemoryStore()
        self.snapshot = Snapshot(self.store, "test-account", self.store.current, None)

    def test_partition_groups_are_bounded_by_declared_bytes_including_exact_limit(self):
        parts = [{"path": f"datasets/tracks/part-{index}.csv", "byte_size": size}
                 for index, size in enumerate((6, 4, 7, 3, 10))]
        self.store.commits[self.store.current] = {
            f"accounts/test-account/{part['path']}": b"x" * part["byte_size"] for part in parts
        }
        with patch("spoty.storage.snapshots.MAX_CSV_BYTES", 10), patch.object(
            self.store, "read_many", wraps=self.store.read_many,
        ) as read_many:
            result = list(self.snapshot._partition_data(parts))
        self.assertEqual([len(call.args[1]) for call in read_many.call_args_list], [2, 2, 1])
        self.assertEqual([len(data) for _, data in result], [6, 4, 7, 3, 10])

    def test_invalid_grouped_result_shape_never_becomes_partial_success(self):
        parts = [{"path": "datasets/tracks/part.csv", "byte_size": 1}]
        for result in ([], [b"x", b"y"], [None], (b"x",)):
            with self.subTest(result=result), patch.object(self.store, "read_many", return_value=result):
                with self.assertRaisesRegex(StorageError, "invalid result"):
                    list(self.snapshot._partition_data(parts))

    def test_checksums_rows_and_unique_keys_still_validated_after_grouped_read(self):
        from spoty.storage.csv_codec import SCHEMAS, encode_csv
        from spoty.storage.snapshots import descriptor

        schema = SCHEMAS["artists"]
        data = encode_csv(schema, [{"id": "a", "name": "Artist"}])
        path = "datasets/artists/bucket-00/part-0000.csv"
        part = descriptor(path, data, schema)
        entry = {"schema_version": 1, "bucket_count": 16, "partitions": [part]}
        manifest = {"schema_version": 5, "datasets": {"artists": entry}}
        snapshot = Snapshot(self.store, "test-account", self.store.current, manifest)
        files = {f"accounts/test-account/{path}": data}
        self.store.commits[self.store.current] = files
        self.assertEqual(snapshot.rows("artists"), [{"id": "a", "name": "Artist"}])
        files[f"accounts/test-account/{path}"] = data + b"\n"
        with self.assertRaisesRegex(ValidationError, "checksum"):
            snapshot.rows("artists")
        files[f"accounts/test-account/{path}"] = data.replace(b"Artist", b"Damage")
        with self.assertRaisesRegex(ValidationError, "checksum"):
            snapshot.rows("artists")
        files[f"accounts/test-account/{path}"] = data
        part["row_count"] = 2
        with self.assertRaisesRegex(ValidationError, "row count"):
            snapshot.rows("artists")
        part["row_count"] = 1
        second_path = "datasets/artists/bucket-01/part-0000.csv"
        entry["partitions"].append(descriptor(second_path, data, schema))
        files[f"accounts/test-account/{second_path}"] = data
        with self.assertRaisesRegex(ValidationError, "Duplicate key"):
            snapshot.rows("artists")
