import os
import subprocess
import sys
import unittest
from pathlib import Path


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
