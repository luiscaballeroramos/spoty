import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from examples.github_data_demo import main, preflight, publish_synthetic, verify_synthetic_recovery
from spoty.cli.credentials import local_github_token
from spoty.storage.contracts import StorageError
from spoty.storage.github import GitHubGitStore, GitHubHTTPError, GitHubResponse
from spoty.domain.listening import ValidationError


class LocalCredentialTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
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
    def check(self, rules, detail, **kwargs):
        store = Mock(owner="demo", repository="private-data", branch="main")
        store.head.return_value = "a" * 40
        transport = Mock(side_effect=[GitHubResponse(200, {}, rules), *[
            GitHubResponse(200, {}, detail) for _ in rules
        ]])
        return preflight(store, transport, **kwargs)

    def rules(self):
        return [
            {"type": kind, "ruleset_id": 1, "ruleset_source_type": "Repository"}
            for kind in ("deletion", "non_fast_forward")
        ]

    def test_enforced_protections_without_bypass_required(self):
        report = self.check(self.rules(), {"enforcement": "active", "bypass_actors": []})
        self.assertTrue(report["branch_rules_verified"])
        self.assertEqual(report["protection_policy"], "strict")
        self.assertEqual(report["residual_risks"], [])

    def test_disabled_missing_or_bypass_rules_prevent_publication(self):
        for rules, detail in (
            ([], {}), (self.rules()[:1], {"enforcement": "active", "bypass_actors": []}),
            (self.rules(), {"enforcement": "disabled", "bypass_actors": []}),
            (self.rules(), {"enforcement": "active", "bypass_actors": [{"actor_type": "RepositoryRole"}]}),
        ):
            with self.subTest(rules=rules, detail=detail), self.assertRaises(StorageError):
                self.check(rules, detail)

    def test_explicit_private_risk_acceptance_does_not_claim_protection(self):
        report = self.check([], {}, policy="allow-unprotected-private")
        self.assertFalse(report["branch_rules_verified"])
        self.assertEqual(report["branch_protections"], {"deletion": False, "non_fast_forward": False})
        self.assertEqual(report["residual_risks"], ["branch_deletion", "force_push"])
        self.assertEqual(report["protection_policy"], "allow-unprotected-private")

    def test_opt_in_reports_partial_disabled_and_bypass_protections_conservatively(self):
        detail = {"enforcement": "active", "bypass_actors": []}
        report = self.check(self.rules()[:1], detail, policy="allow-unprotected-private")
        self.assertEqual(report["branch_protections"], {"deletion": True, "non_fast_forward": False})
        self.assertEqual(report["residual_risks"], ["force_push"])
        for detail in (
            {"enforcement": "disabled", "bypass_actors": []},
            {"enforcement": "evaluate", "bypass_actors": []},
            {"enforcement": "active", "bypass_actors": [{"actor_type": "RepositoryRole"}]},
        ):
            with self.subTest(detail=detail):
                report = self.check(self.rules(), detail, policy="allow-unprotected-private")
                self.assertFalse(report["branch_rules_verified"])
                self.assertFalse(any(report["branch_protections"].values()))

    def test_opt_in_keeps_verified_protections_and_deduplicates_ruleset_reads(self):
        store = Mock(owner="demo", repository="private-data", branch="main")
        store.head.return_value = "a" * 40
        transport = Mock(side_effect=[
            GitHubResponse(200, {}, self.rules()),
            GitHubResponse(200, {}, {"enforcement": "active", "bypass_actors": []}),
        ])
        report = preflight(store, transport, policy="allow-unprotected-private")
        self.assertTrue(report["branch_rules_verified"])
        self.assertEqual(report["residual_risks"], [])
        self.assertEqual(transport.call_count, 2)
        self.assertTrue(all(call.args[0] == "GET" for call in transport.call_args_list))

    def test_unknown_policy_fails_before_network(self):
        store, transport = Mock(), Mock()
        with self.assertRaises(ValidationError):
            preflight(store, transport, policy="permissive")
        store.head.assert_not_called()
        transport.assert_not_called()

    def test_public_archived_and_wrong_repository_rejected_even_with_opt_in(self):
        for repository in (
            {"private": False, "archived": False, "full_name": "demo/private-data"},
            {"private": True, "archived": True, "full_name": "demo/private-data"},
            {"private": True, "archived": False, "full_name": "demo/other"},
        ):
            with self.subTest(repository=repository):
                transport = Mock(return_value=GitHubResponse(200, {}, repository))
                store = GitHubGitStore("demo", "private-data", transport)
                with self.assertRaises(StorageError):
                    preflight(store, transport, policy="allow-unprotected-private")
                self.assertEqual(transport.call_count, 1)

    def test_http_errors_never_become_an_unprotected_success(self):
        store = Mock(owner="demo", repository="private-data", branch="main")
        for status in (301, 401, 403, 404, 429, 500):
            for detail_error in (False, True):
                with self.subTest(status=status, detail_error=detail_error):
                    responses = [GitHubResponse(status, {}, None)]
                    if detail_error:
                        responses.insert(0, GitHubResponse(200, {}, self.rules()))
                    with self.assertRaises(StorageError):
                        preflight(store, Mock(side_effect=responses), policy="allow-unprotected-private")

    def test_exact_plan_denial_requires_explicit_private_risk_policy(self):
        response = GitHubResponse(403, {}, None, "private_rules_require_plan_upgrade")
        store = Mock(owner="demo", repository="private-data", branch="main")
        store.head.return_value = "a" * 40
        transport = Mock(return_value=response)
        with self.assertRaises(GitHubHTTPError):
            preflight(store, transport)
        report = preflight(store, transport, policy="allow-unprotected-private")
        self.assertEqual(report["rules_verification"], "unavailable_on_current_plan")
        self.assertFalse(report["branch_rules_verified"])
        self.assertEqual(report["branch_protections"], {"deletion": None, "non_fast_forward": None})
        self.assertEqual(report["residual_risks"], ["branch_deletion", "force_push"])
        store.head.assert_called()
        self.assertTrue(all(call.args[0] == "GET" for call in transport.call_args_list))

    def test_classification_never_allows_other_statuses_or_ruleset_detail_errors(self):
        store = Mock(owner="demo", repository="private-data", branch="main")
        for status in (401, 404, 429, 500):
            with self.subTest(status=status), self.assertRaises(GitHubHTTPError):
                preflight(store, Mock(return_value=GitHubResponse(
                    status, {}, None, "private_rules_require_plan_upgrade",
                )), policy="allow-unprotected-private")
        with self.assertRaises(GitHubHTTPError):
            preflight(store, Mock(side_effect=[
                GitHubResponse(200, {}, self.rules()),
                GitHubResponse(403, {}, None, "private_rules_require_plan_upgrade"),
            ]), policy="allow-unprotected-private")

    def test_incomplete_or_unverifiable_rules_fail_even_with_opt_in(self):
        for rules, detail in (
            ({}, {}), ([{}], {}), ([{"type": "deletion"}], {}),
            (self.rules() * 50, {}),
            ([{"type": "deletion", "ruleset_id": 1, "ruleset_source_type": "Organization"}], {}),
            (self.rules(), {"enforcement": "active"}),
            (self.rules(), {"enforcement": "unknown", "bypass_actors": []}),
            (self.rules(), {"enforcement": [], "bypass_actors": []}),
        ):
            with self.subTest(rules=rules, detail=detail), self.assertRaises(StorageError):
                self.check(rules, detail, policy="allow-unprotected-private")


class SyntheticCommandTest(unittest.TestCase):
    def test_remote_demo_and_recovery_with_fake_git_objects(self):
        from tests.test_github_data_store import FakeGitHub

        api = FakeGitHub()
        store = GitHubGitStore("demo", "private-data", api)
        first = publish_synthetic(store, "synthetic-recovery")
        # A fresh adapter must recover using persisted coordination, not cached state.
        recovered = verify_synthetic_recovery(GitHubGitStore("demo", "private-data", api), "synthetic-recovery")
        self.assertTrue(first["stale_head_rejected"])
        self.assertTrue(recovered["favorite_recovery_observation_only"])
        self.assertTrue(recovered["historical_favorite_preserved"])
        self.assertEqual(recovered["removed_favorites"], 1)
        self.assertEqual(recovered["incremental_metadata_saved"], 1)
        self.assertEqual(recovered["pending_batches"], 0)
        with self.assertRaises(StorageError):
            verify_synthetic_recovery(store, "synthetic-missing")
        with self.assertRaises(ValidationError):
            verify_synthetic_recovery(store, "production")

    def test_recovery_cli_requires_synthetic_account_before_access(self):
        with patch("examples.github_data_demo.RequestsGitHubTransport") as transport:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([
                    "--owner", "demo", "--repository", "private-data",
                    "--verify-synthetic-recovery", "--account", "production",
                ]), 1)
        transport.assert_not_called()

    def test_recovery_cli_is_explicit_and_runs_after_preflight_only(self):
        with patch("examples.github_data_demo.RequestsGitHubTransport"):
            with patch("examples.github_data_demo.GitHubGitStore") as store:
                with patch("examples.github_data_demo.preflight", return_value={"ready": True}) as check:
                    with patch("examples.github_data_demo.verify_synthetic_recovery", return_value={}) as recovery:
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(main([
                                "--owner", "demo", "--repository", "private-data",
                                "--verify-synthetic-recovery", "--account", "synthetic-demo",
                            ]), 0)
        check.assert_called_once()
        recovery.assert_called_once_with(store.return_value, "synthetic-demo")

    def test_synthetic_namespace_validated_before_any_transport_or_credentials(self):
        with patch("examples.github_data_demo.RequestsGitHubTransport") as transport:
            with patch("examples.github_data_demo.local_github_token") as token:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    status = main([
                        "--owner", "demo", "--repository", "private-data",
                        "--publish-synthetic", "--account", "production",
                        "--protection-policy", "allow-unprotected-private",
                    ])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")
        transport.assert_not_called()
        token.assert_not_called()

    def test_publication_function_rejects_non_synthetic_and_occupied_namespaces(self):
        store = Mock()
        with self.assertRaises(ValidationError):
            publish_synthetic(store, "production")
        store.head.assert_not_called()
        store.head.return_value = "a" * 40
        store.list_paths.return_value = ["accounts/synthetic-used/inbox/example.csv"]
        with self.assertRaises(StorageError):
            publish_synthetic(store, "synthetic-used")
        store.publish_batch.assert_not_called()
        store.publish.assert_not_called()

    def test_preflight_command_defaults_to_strict_and_does_not_publish(self):
        for policy in ("strict", "allow-unprotected-private"):
            arguments = ["--owner", "demo", "--repository", "private-data"]
            if policy != "strict":
                arguments += ["--protection-policy", policy]
            with patch("examples.github_data_demo.RequestsGitHubTransport"):
                with patch("examples.github_data_demo.GitHubGitStore"):
                    with patch("examples.github_data_demo.preflight", return_value={"ready": True}) as check:
                        with patch("examples.github_data_demo.publish_synthetic") as publish:
                            with contextlib.redirect_stdout(io.StringIO()):
                                self.assertEqual(main(arguments), 0)
            self.assertEqual(check.call_args.kwargs, {"policy": policy})
            publish.assert_not_called()

    def test_provider_error_details_are_not_printed(self):
        with patch("examples.github_data_demo.RequestsGitHubTransport"):
            with patch("examples.github_data_demo.GitHubGitStore", side_effect=StorageError("secret-token")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--owner", "demo", "--repository", "private-data"]), 1)
        self.assertNotIn("secret-token", output.getvalue())

    def test_http_status_is_reported_without_response_or_credentials(self):
        with patch("examples.github_data_demo.RequestsGitHubTransport"):
            with patch("examples.github_data_demo.GitHubGitStore", side_effect=GitHubHTTPError(403)):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["--owner", "demo", "--repository", "private-data"]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["http_status"], 403)
        self.assertEqual(report["error_type"], "GitHubHTTPError")
