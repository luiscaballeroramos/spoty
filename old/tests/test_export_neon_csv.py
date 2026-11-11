import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import export_neon_csv


class NeonCSVExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "export"
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = [
            ("public", 'track"data', "r"), ("other", "events", "p"),
        ]
        self.cursor.copy.return_value.__enter__.side_effect = [
            iter([b'id,name\n1,"hello,', b' world"\n']), iter([b"id\n"]),
        ]
        connect_patch = patch.object(export_neon_csv.psycopg, "connect")
        self.connect = connect_patch.start()
        self.addCleanup(connect_patch.stop)
        self.connect.return_value.__enter__.return_value = self.connection
        url_patch = patch.object(
            export_neon_csv, "require_database_url", return_value="mock-url"
        )
        url_patch.start()
        self.addCleanup(url_patch.stop)

    def test_streams_tables_in_one_read_only_snapshot(self):
        """Use one connection, native CSV blocks and one consistent read-only snapshot."""
        self.assertEqual(export_neon_csv.export_tables(self.output), 2)
        self.connect.assert_called_once()
        statements = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertEqual(statements[0],
                         "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertIn("SET LOCAL row_security = off", statements)
        self.assertEqual(statements[-1], export_neon_csv.TABLES_QUERY)
        copies = [call.args[0].as_string() for call in self.cursor.copy.call_args_list]
        self.assertIn('SELECT * FROM ONLY "public"."track""data"', copies[0])
        self.assertIn('SELECT * FROM "other"."events"', copies[1])
        self.assertTrue(all("FORMAT CSV, HEADER TRUE" in query for query in copies))
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(len(manifest["tables"]), 2)
        self.assertEqual(
            (self.output / manifest["tables"][0]["file"]).read_bytes(),
            b'id,name\n1,"hello, world"\n',
        )
        self.assertFalse(list(self.output.rglob("*.part")))
        self.connection.transaction.return_value.__exit__.assert_called_once()
        self.connect.return_value.__exit__.assert_called_once()

    def test_failure_retains_partial_without_completion_manifest(self):
        """Never label an interrupted stream as a complete table or database export."""
        def broken_stream():
            yield b"id\n"
            raise RuntimeError("connection lost")

        self.cursor.copy.return_value.__enter__.side_effect = [broken_stream()]
        with self.assertRaises(RuntimeError):
            export_neon_csv.export_tables(self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(len(list(self.output.rglob("*.part"))), 1)
        self.assertFalse(list(self.output.rglob("*.csv")))
        self.connect.return_value.__exit__.assert_called_once()

    def test_existing_directory_is_not_overwritten(self):
        """Refuse to replace an earlier export before opening a database connection."""
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            export_neon_csv.export_tables(self.output)
        self.connect.assert_not_called()

    def test_cli_failure_does_not_print_provider_secrets(self):
        """Return a failure code without leaking the DSN in exception text."""
        with (
            patch.object(export_neon_csv, "export_tables", side_effect=RuntimeError("SECRET")),
            patch("sys.stderr") as stderr,
        ):
            self.assertEqual(export_neon_csv.main(["--output", str(self.output)]), 1)
        self.assertNotIn("SECRET", str(stderr.write.call_args_list))
