import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from examples.github_data_demo import preflight
from spoty.cli.credentials import local_github_token
from spoty.storage.contracts import StorageError
from spoty.storage.github import GitHubResponse


class LocalCredentialTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(text, encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                result = local_github_token(path)
                self.assertEqual(dict(os.environ), {})
                return result

    def test_only_selected_settings_loaded_and_quotes_supported(self):
        self.assertEqual(self.load('OTHER_SECRET=ignored\nexport GH_TOKEN="synthetic-token"\n'), "synthetic-token")
        self.assertEqual(self.load("GITHUB_TOKEN='synthetic-token'\n"), "synthetic-token")

    def test_environment_precedence_and_no_implicit_file(self):
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic"}, clear=True):
            self.assertEqual(local_github_token(Path("missing")), "synthetic")
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(StorageError):
            local_github_token()

    def test_missing_empty_duplicate_and_whitespace_credentials_raise_sanitized_errors(self):
        for text in ("OTHER=value\n", "GH_TOKEN=\n", "GH_TOKEN=one\nGH_TOKEN=two\n", "GH_TOKEN='two words'\n"):
            with self.subTest(text=text), self.assertRaises(StorageError):
                self.load(text)


class PreflightTest(unittest.TestCase):
    def check(self, rules, detail):
        store = Mock(owner="demo", repository="private-data", branch="main")
        store.head.return_value = "a" * 40
        transport = Mock(side_effect=[GitHubResponse(200, {}, rules), *[
            GitHubResponse(200, {}, detail) for _ in rules
        ]])
        return preflight(store, transport)

    def rules(self):
        return [
            {"type": kind, "ruleset_id": 1, "ruleset_source_type": "Repository"}
            for kind in ("deletion", "non_fast_forward")
        ]

    def test_enforced_protections_without_bypass_required(self):
        report = self.check(self.rules(), {"enforcement": "active", "bypass_actors": []})
        self.assertTrue(report["branch_rules_verified"])

    def test_disabled_missing_or_bypass_rules_prevent_publication(self):
        for rules, detail in (
            ([], {}), (self.rules()[:1], {"enforcement": "active", "bypass_actors": []}),
            (self.rules(), {"enforcement": "disabled", "bypass_actors": []}),
            (self.rules(), {"enforcement": "active", "bypass_actors": [{"actor_type": "RepositoryRole"}]}),
        ):
            with self.subTest(rules=rules, detail=detail), self.assertRaises(StorageError):
                self.check(rules, detail)
