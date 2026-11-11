import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import psycopg
import yaml

from examples.csv_pipeline_demo import run_demo
from spoty.storage.staging import FileChanges


class CsvPipelineTest(unittest.TestCase):
    def test_acquisition_consolidation_queries_recovery_and_audit_without_http(self):
        with patch.object(requests.sessions.Session, "request", side_effect=AssertionError("Unexpected HTTP")), \
                patch.object(psycopg, "connect", side_effect=AssertionError("Unexpected SQL")):
            report = run_demo()
        self.assertEqual(report["verified_captures"], 5)
        self.assertEqual(report["counts"]["total_liked_tracks"], 0)
        self.assertEqual(report["pending_batches"], 0)
        self.assertTrue(report["historical_snapshots_unchanged"])
        self.assertTrue(report["favorite_recovery_observation_only"])

    def test_temporary_metadata_staging_is_removed_after_publication_failure(self):
        create = FileChanges.__init__
        directories = []

        def remember(instance, directory):
            directories.append(directory)
            create(instance, directory)

        with patch.object(FileChanges, "__init__", remember), \
                patch.object(FileChanges, "__getitem__", side_effect=OSError("Synthetic disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                run_demo()
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories))

    def test_validation_workflow_is_manual_read_only_and_has_no_data_secrets(self):
        path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "csv-validation.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("spoty-data", text)
        steps = workflow["jobs"]["synthetic-validation"]["steps"]
        self.assertFalse(steps[0]["with"]["persist-credentials"])
        self.assertTrue(any("examples.csv_pipeline_demo" in step.get("run", "") for step in steps))
