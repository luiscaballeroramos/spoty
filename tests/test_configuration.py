import os
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


class ConfigurationImportTests(unittest.TestCase):
    def test_database_analysis_imports_without_spotify_or_database_access(self):
        """Import analysis modules without Spotify credentials or opening external connections."""
        environment = os.environ.copy()
        environment.update({
            "DATABASE_URL": "",
            "SPOTIFY_CLIENT_ID": "",
            "SPOTIFY_CLIENT_SECRET": "",
        })
        script = """
import psycopg
import requests
from unittest.mock import patch

with (
    patch.object(psycopg, "connect", side_effect=AssertionError("import opened a DB connection")),
    patch.object(requests.sessions.Session, "request", side_effect=AssertionError("import made an HTTP request")),
):
    import analysis_listeningevents
    import analysis_topindividual
    import config

assert not config.CLIENT_ID
assert not config.CLIENT_SECRET
try:
    config.require_database_url()
except ValueError as error:
    assert "Missing DATABASE_URL" in str(error)
else:
    raise AssertionError("Missing DATABASE_URL was not validated at use")
"""
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )


@unittest.skipUnless(yaml, "Install the test extra to validate workflows")
class WorkflowConfigurationTests(unittest.TestCase):
    def test_daily_workflows_have_independent_schedules_and_concurrency(self):
        """Keep liked tracks and each metadata source operationally independent."""
        folder = Path(__file__).resolve().parents[1] / ".github/workflows"
        groups = set()
        schedules = set()
        workflows = ("liked_tracks.yml", "metadata_lastfm.yml", "metadata_musicbrainz.yml",
                 "metadata_acousticbrainz.yml", "smarterplaylists.yml")
        for name in workflows:
            with self.subTest(workflow=name):
                workflow = yaml.load((folder / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
                self.assertIn("workflow_dispatch", workflow["on"])
                schedules.add(workflow["on"]["schedule"][0]["cron"])
                groups.add(workflow["concurrency"]["group"])
                self.assertEqual(workflow["permissions"]["contents"], "read")
                self.assertEqual(len(workflow["jobs"]), 1)
                for job in workflow["jobs"].values():
                    self.assertNotIn("needs", job)
                    self.assertIn("timeout-minutes", job)
        self.assertEqual(len(groups), 5)
        self.assertEqual(len(schedules), 5)
        self.assertFalse((folder / "metadata_enrichment.yml").exists())

    def test_each_metadata_workflow_selects_only_its_source(self):
        """Run provider-specific commands without supplying other sources' secrets."""
        folder = Path(__file__).resolve().parents[1] / ".github/workflows"
        for provider in ("lastfm", "musicbrainz", "acousticbrainz"):
            with self.subTest(provider=provider):
                path = folder / f"metadata_{provider}.yml"
                workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
                step = workflow["jobs"][provider]["steps"][-1]
                self.assertIn(f"spoty-enrich --provider {provider} --limit 50", step["run"])
                self.assertEqual("LASTFM_API_KEY" in step["env"], provider == "lastfm")
                self.assertNotIn("SPOTIFY_CLIENT_SECRET", step["env"])
                self.assertNotIn("SMARTERPLAYLISTS_SESSION_COOKIE", step["env"])

    def test_build_smoke_test_is_a_separate_ci_step(self):
        """Do not accidentally fold the wheel build into the Python compile command."""
        path = Path(__file__).resolve().parents[1] / ".github/workflows/tests.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = {step.get("name"): step for step in workflow["jobs"]["tests"]["steps"]}
        self.assertIn("Build and smoke-test installed wheel", steps)
        self.assertNotIn("- name:", steps["Check Python syntax"]["run"])
