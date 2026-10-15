import importlib.util
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch


class SimpleDBLifecycleTest(unittest.TestCase):
    def _load_simple_db(self):
        config = ModuleType("config")
        config.DBSCHEMA = {}
        config.VERBOSE = False
        config.require_database_url = lambda database_url=None: database_url
        module_name = "_spoty_test_register_db"
        path = Path(__file__).resolve().parents[1] / "register" / "db.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"config": config, module_name: module}):
            spec.loader.exec_module(module)
        return module

    def _database_with_cursor(self):
        db_module = self._load_simple_db()
        database = object.__new__(db_module.SimpleDB)
        database.conn = Mock()
        database.conn.transaction.return_value = nullcontext()
        database.cursor = Mock()
        database.insert = Mock()
        database.get_all_ids = Mock()
        database.delete_ids = Mock()
        return database

    def test_context_closes_cursor_and_connection_after_success(self):
        """Verify Context closes cursor and connection after success."""
        db_module = self._load_simple_db()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor

        with patch.object(db_module.psycopg, "connect", return_value=connection):
            with db_module.SimpleDB("unused") as database:
                self.assertIs(database.conn, connection)

        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_constructor_does_not_create_or_modify_schema(self):
        """Verify Constructor does not create or modify schema."""
        db_module = self._load_simple_db()
        db_module.DBSCHEMA = {"example": {"id": "TEXT PRIMARY KEY"}}
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor

        with patch.object(
            db_module.psycopg, "connect", return_value=connection
        ) as connect:
            database = db_module.SimpleDB("unused")

        cursor.execute.assert_not_called()
        connection.commit.assert_not_called()
        connect.assert_called_once_with(
            "unused", row_factory=db_module.dict_row, autocommit=True
        )
        database.close()

    def test_related_writes_use_one_explicit_transaction_without_inner_commits(self):
        """Verify Related writes use one explicit transaction without inner commits."""
        db_module = self._load_simple_db()
        connection = Mock()
        cursor = Mock()
        cursor.fetchone.side_effect = [(1,), (1,)]
        connection.cursor.return_value = cursor
        connection.transaction.return_value = nullcontext()

        with patch.object(db_module.psycopg, "connect", return_value=connection):
            with db_module.SimpleDB("unused") as database:
                with database.transaction():
                    database.insert("artists", {"id": "artist-id"})
                    database.insert("albums", {"id": "album-id"})

        connection.transaction.assert_called_once_with()
        connection.commit.assert_not_called()

    def test_transaction_context_rolls_back_on_exception(self):
        """Verify Transaction context rolls back on exception."""
        db_module = self._load_simple_db()

        class Transaction:
            rolled_back = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.rolled_back = exc_type is not None
                return False

        transaction = Transaction()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        connection.transaction.return_value = transaction

        with patch.object(db_module.psycopg, "connect", return_value=connection):
            with db_module.SimpleDB("unused") as database:
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    with database.transaction():
                        raise RuntimeError("write failed")

        self.assertTrue(transaction.rolled_back)

    def test_reserve_liked_sync_returns_generation_and_local_revision(self):
        """Verify Reserve liked sync returns generation and local revision."""
        database = self._database_with_cursor()
        database.cursor.fetchone.return_value = (4, 7)

        self.assertEqual(database.reserve_liked_track_sync(), (4, 7))
        self.assertIn(
            "generation = generation + 1",
            str(database.cursor.execute.call_args.args[0]),
        )
        database.conn.transaction.assert_called_once_with()

    def test_stale_liked_snapshot_is_not_applied(self):
        """Verify Stale liked snapshot is not applied."""
        database = self._database_with_cursor()
        database.cursor.fetchone.return_value = (5, 8)

        applied = database.apply_liked_track_snapshot(4, 8, ["new-track"])

        self.assertEqual(applied, (False, 0, []))
        database.insert.assert_not_called()
        database.get_all_ids.assert_not_called()
        database.delete_ids.assert_not_called()

    def test_current_liked_snapshot_applies_within_one_transaction(self):
        """Verify Current liked snapshot applies within one transaction."""
        database = self._database_with_cursor()
        database.cursor.fetchone.return_value = (5, 8, 0)
        database.insert.return_value = True
        database.get_all_ids.return_value = ["current", "stale"]

        result = database.apply_liked_track_snapshot(5, 8, ["current"])

        self.assertEqual(result, (True, 1, ["stale"]))
        database.insert.assert_called_once_with(
            "liked_tracks", {"id": "current"}, print_only_insert=True
        )
        database.delete_ids.assert_called_once_with("liked_tracks", ["stale"])
        database.conn.transaction.assert_called_once_with()

    def test_liked_snapshot_is_rejected_after_local_revision_changes(self):
        """Verify Liked snapshot is rejected after local revision changes."""
        database = self._database_with_cursor()
        database.cursor.fetchone.return_value = (5, 9, 0)

        result = database.apply_liked_track_snapshot(5, 8, ["stale-view"])

        self.assertEqual(result, (False, 0, []))
        database.insert.assert_not_called()
        database.delete_ids.assert_not_called()

    def test_liked_snapshot_cannot_be_applied_twice(self):
        """Verify Liked snapshot cannot be applied twice."""
        database = self._database_with_cursor()
        database.cursor.fetchone.return_value = (5, 8, 5)

        result = database.apply_liked_track_snapshot(5, 8, ["track-id"])

        self.assertEqual(result, (False, 0, []))
        database.insert.assert_not_called()
        database.delete_ids.assert_not_called()

    def test_local_liked_change_advances_revision_in_same_transaction(self):
        """Verify Local liked change advances revision in same transaction."""
        database = self._database_with_cursor()

        database.set_liked_track("track-id", True)

        database.insert.assert_called_once_with(
            "liked_tracks", {"id": "track-id"}, print_only_insert=True
        )
        statements = [str(call.args[0]) for call in database.cursor.execute.call_args_list]
        self.assertTrue(any("local_revision = local_revision + 1" in sql for sql in statements))
        database.conn.transaction.assert_called_once_with()

    def test_context_closes_cursor_and_connection_after_exception(self):
        """Verify Context closes cursor and connection after exception."""
        db_module = self._load_simple_db()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor

        with patch.object(db_module.psycopg, "connect", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "operation failed"):
                with db_module.SimpleDB("unused"):
                    raise RuntimeError("operation failed")

        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


class MigrationRunnerTest(unittest.TestCase):
    def test_initial_schema_migration_is_versioned_and_idempotent(self):
        """Verify Initial schema migration is versioned and idempotent."""
        from register.migrations import EXPECTED_COLUMNS, MIGRATIONS, apply_migrations

        class FakeCursor:
            def __init__(self):
                self.applied_versions = set()
                self.statements = []
                self.rows = []
                self.row = None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def execute(self, statement, parameters=None):
                statement = str(statement)
                self.statements.append(statement)
                if statement.startswith("SELECT version FROM schema_migrations"):
                    self.rows = [(version,) for version in self.applied_versions]
                elif "FROM information_schema.columns" in statement:
                    self.rows = [
                        (table, column)
                        for table, columns in EXPECTED_COLUMNS.items()
                        for column in columns
                    ]
                elif "SELECT EXISTS" in statement:
                    self.row = (True,)
                elif statement.startswith("INSERT INTO schema_migrations"):
                    self.applied_versions.add(parameters[0])

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.row

        class FakeConnection:
            def __init__(self):
                self.fake_cursor = FakeCursor()
                self.transaction_count = 0

            def transaction(self):
                self.transaction_count += 1
                return nullcontext()

            def cursor(self):
                return self.fake_cursor

        connection = FakeConnection()

        self.assertEqual(
            apply_migrations(connection),
            ["0001_initial_schema", "0002_metadata_enrichment", "0003_smarterplaylists_metadata",
             "0004_smarterplaylists_refresh_state"],
        )
        creates_before_second_run = sum(
            "CREATE TABLE IF NOT EXISTS listening_events" in statement
            for statement in connection.fake_cursor.statements
        )
        self.assertEqual(apply_migrations(connection), [])
        creates_after_second_run = sum(
            "CREATE TABLE IF NOT EXISTS listening_events" in statement
            for statement in connection.fake_cursor.statements
        )

        self.assertEqual(creates_before_second_run, 1)
        self.assertEqual(creates_after_second_run, 1)
        self.assertEqual(connection.transaction_count, 2)
        self.assertEqual(len(MIGRATIONS), 4)
