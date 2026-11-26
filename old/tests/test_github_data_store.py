import base64
import hashlib
import unittest
from unittest.mock import MagicMock, patch

from spoty.domain.listening import ListeningBatch, ValidationError, canonical_json
from spoty.domain.metadata import MetadataBatch
from examples.inspect_spoty_data import inspect_repository
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoriteOperationPending, FavoriteQueryService, FavoritesCoordinator
from spoty.storage.contracts import PublicationConflict, StorageError
from spoty.storage.github import (
    GitHubGitStore, GitHubHTTPError, GitHubResponse, RequestsGitHubTransport, blob_sha,
)
from spoty.storage.snapshots import Snapshot


ACCOUNT = "account-demo"
TRACK = "T" * 22
DATE = "2026-11-06T10:00:00Z"


def listening():
    artist = {"id": "R" * 22, "name": "Demo artist"}
    return ListeningBatch.from_items(ACCOUNT, "test", DATE, [{
        "played_at": DATE, "context": None,
        "track": {"id": TRACK, "name": "Demo", "duration_ms": 1000, "artists": [artist],
                  "album": {"id": "A" * 22, "name": "Album", "artists": [artist], "total_tracks": 1}},
    }])


def metadata():
    values = {"id": TRACK, "uri": f"spotify:track:{TRACK}", "name": "Demo", "energy": 0}
    return MetadataBatch.from_smarterplaylists(ACCOUNT, "pulse", DATE, [{
        "captured_at": DATE, "entities": {"track": {
            "values": values, "conflicts": {}, "missing_requested": [],
            "raw": {"status": "complete", "playlist_uri": None, "tracks": [values]},
        }},
    }], live_track=True)


class FakeGitHub:
    """In-memory Git object server; no real credentials, HTTP or repository."""

    def __init__(self):
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.calls = []
        self.private = True
        self.full_name = "demo/private-data"
        self.before_patch = self.after_patch = None
        self.error = None
        self.malformed = None
        self.head = self.commit(self.tree({}), [])

    def object_sha(self, value):
        return hashlib.sha1(canonical_json(value).encode()).hexdigest()

    def tree(self, files):
        grouped = {}
        entries = []
        for path, sha in sorted(files.items()):
            first, _, rest = path.partition("/")
            if rest:
                grouped.setdefault(first, {})[rest] = sha
            else:
                entries.append({"path": first, "sha": sha, "mode": "100644", "type": "blob"})
        for name, children in grouped.items():
            entries.append({"path": name, "sha": self.tree(children), "mode": "040000", "type": "tree"})
        entries.sort(key=lambda entry: entry["path"])
        sha = self.object_sha(entries)
        self.trees[sha] = entries
        return sha

    def files(self, sha, prefix=""):
        result = {}
        for entry in self.trees[sha]:
            if entry["type"] == "tree":
                result.update(self.files(entry["sha"], prefix + entry["path"] + "/"))
            else:
                result[prefix + entry["path"]] = entry["sha"]
        return result

    def commit(self, tree, parents):
        sha = self.object_sha({"tree": tree, "parents": parents, "index": len(self.commits)})
        self.commits[sha] = {"sha": sha, "tree": {"sha": tree}, "parents": parents}
        return sha

    def concurrent(self, path="unrelated.csv", data=b"other"):
        sha = blob_sha(data)
        self.blobs[sha] = data
        files = self.files(self.commits[self.head]["tree"]["sha"])
        files[path] = sha
        self.head = self.commit(self.tree(files), [self.head])

    def __call__(self, method, url, body):
        path = url.removeprefix("https://api.github.com/repos/demo/private-data")
        self.calls.append((method, path, body))
        if self.error:
            response = self.error(method, path, body)
            if response:
                return response
        status = 200
        if method == "GET" and path == "":
            result = {"private": self.private, "archived": False, "full_name": self.full_name}
        elif method == "GET" and path == "/git/ref/heads/main":
            result = {"ref": "refs/heads/main", "object": {"sha": self.head, "type": "commit"}}
        elif method == "GET" and path.startswith("/git/commits/"):
            result = self.commits[path.rsplit("/", 1)[1]]
        elif method == "GET" and path.startswith("/git/trees/"):
            sha = path.rsplit("/", 1)[1]
            result = {"sha": sha, "truncated": False, "tree": self.trees[sha]}
        elif method == "GET" and path.startswith("/git/blobs/"):
            sha = path.rsplit("/", 1)[1]
            data = self.blobs[sha]
            result = {"sha": sha, "encoding": "base64", "content": base64.b64encode(data).decode(),
                      "size": len(data)}
        elif method == "POST" and path == "/git/blobs":
            data = base64.b64decode(body["content"], validate=True)
            sha = blob_sha(data)
            self.blobs[sha] = data
            status, result = 201, {"sha": sha}
        elif method == "POST" and path == "/git/trees":
            files = self.files(body["base_tree"])
            for entry in body["tree"]:
                if entry["sha"] is None:
                    if entry["path"] not in files:
                        return GitHubResponse(422, {}, None)
                    del files[entry["path"]]
                else:
                    files[entry["path"]] = entry["sha"]
            status, result = 201, {"sha": self.tree(files)}
        elif method == "POST" and path == "/git/commits":
            status, result = 201, {"sha": self.commit(body["tree"], body["parents"])}
        elif method == "PATCH" and path == "/git/refs/heads/main":
            if self.before_patch:
                callback, self.before_patch = self.before_patch, None
                callback()
            if body["force"] is not False:
                raise AssertionError("Force update attempted")
            if self.head not in self.commits[body["sha"]]["parents"]:
                return GitHubResponse(422, {}, None)
            self.head = body["sha"]
            if self.after_patch:
                callback, self.after_patch = self.after_patch, None
                callback()
            result = {"ref": "refs/heads/main", "object": {"sha": self.head, "type": "commit"}}
        else:
            raise AssertionError((method, path))
        if self.malformed:
            result = self.malformed(method, path, result)
        return GitHubResponse(status, {}, result)


class GitHubStoreTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.store = GitHubGitStore("demo", "private-data", self.api)

    def test_explicit_destination_and_existing_private_branch_required(self):
        for args in (("demo", "../data"), ("luiscaballeroramos", "spoty"), ("demo", "data", "bad/branch")):
            with self.assertRaises(ValidationError):
                GitHubGitStore(args[0], args[1], self.api, branch=args[2] if len(args) == 3 else "main")
        self.api.private = False
        with self.assertRaises(StorageError):
            self.store.publish(self.api.head, {"new.csv": b"data"}, "test")
        self.assertFalse(any(method == "POST" for method, _, _ in self.api.calls))
        with self.assertRaises(ValidationError):
            self.store.publish(None, {}, "Initialize")

    def test_atomic_multiple_files_delete_and_historical_reads(self):
        start = self.store.head()
        first = self.store.publish(start, {"one.csv": b"one", "folder/two.csv": b"two"}, "First")
        self.assertEqual(self.store.list_paths(start), [])
        self.assertEqual(self.store.list_paths(first), ["folder/two.csv", "one.csv"])
        second = self.store.publish(first, {"one.csv": None, "folder/two.csv": b"updated"}, "Second")
        self.assertEqual(self.store.read(first, "folder/two.csv"), b"two")
        self.assertEqual(self.store.read(second, "folder/two.csv"), b"updated")
        self.assertEqual(self.store.list_paths(second), ["folder/two.csv"])
        commits = [body for method, path, body in self.api.calls if method == "POST" and path == "/git/commits"]
        self.assertEqual(commits[-1]["parents"], [first])
        self.assertIn("Co-authored-by: Copilot", commits[-1]["message"])

    def test_prefix_lookup_has_directory_not_string_prefix_semantics(self):
        commit = self.store.publish(self.store.head(), {
            "folder/one.csv": b"one", "folder-other/two.csv": b"two",
        }, "Paths")
        self.assertEqual(self.store.list_paths(commit, "folder"), ["folder/one.csv"])
        self.assertEqual(self.store.list_paths(commit, "folder/one.csv"), ["folder/one.csv"])
        self.assertEqual(self.store.list_paths(commit, "missing"), [])
        with self.assertRaises(ValidationError):
            self.store.read(commit, "../secret")
        with self.assertRaises(StorageError):
            self.store.read(commit, "missing")

    def test_noop_and_missing_delete_do_not_create_commits(self):
        first = self.store.publish(self.store.head(), {"one.csv": b"one"}, "First")
        count = len(self.api.commits)
        self.assertEqual(self.store.publish(first, {"one.csv": b"one", "absent.csv": None}, "Noop"), first)
        self.assertEqual(len(self.api.commits), count)

    def test_stale_head_and_race_during_ref_update_never_overwrite_other_data(self):
        first = self.store.head()
        self.api.concurrent()
        with self.assertRaises(PublicationConflict):
            self.store.publish(first, {"one.csv": b"one"}, "Stale")
        current = self.api.head
        self.api.before_patch = lambda: self.api.concurrent("concurrent.csv")
        with self.assertRaises(PublicationConflict):
            self.store.publish(current, {"one.csv": b"one"}, "Race")
        self.assertEqual(self.store.list_paths(self.api.head), ["concurrent.csv", "unrelated.csv"])

    def test_batch_race_retries_and_keeps_unrelated_commit(self):
        self.api.before_patch = self.api.concurrent
        result = self.store.publish_batch(listening())
        self.assertEqual(result.status, "saved")
        self.assertIn("unrelated.csv", self.store.list_paths(result.commit_id))
        self.assertEqual(self.store.publish_batch(listening()).status, "already_saved")

    def test_lost_acknowledgment_recovers_same_batch(self):
        def uncertain():
            raise StorageError("Simulated lost acknowledgment")

        self.api.after_patch = uncertain
        with self.assertRaises(StorageError):
            self.store.publish_batch(listening())
        self.assertEqual(self.store.publish_batch(listening()).status, "already_saved")
        self.assertEqual(Snapshot.load(self.store, ACCOUNT).pending_batches(), 1)

    def test_read_only_repository_inventory_reports_counts_without_writing(self):
        self.store.publish_batch(listening())
        consolidate(self.store, ACCOUNT)
        self.api.calls.clear()

        report = inspect_repository(self.store)

        account = report["accounts"][0]
        self.assertEqual(report["accounts_count"], 1)
        self.assertEqual(account["account"], ACCOUNT)
        self.assertTrue(account["manifest_present"])
        self.assertEqual(account["datasets"]["listening_events"]["rows"], 1)
        self.assertEqual(account["pending_batches"], 0)
        self.assertEqual(account["archived_batches"], 1)
        self.assertFalse(report["partition_contents_verified"])
        self.assertFalse(any(
            method in {"POST", "PATCH"} for method, _, _ in self.api.calls
        ))

    def test_all_capture_kinds_consolidate_using_same_contract(self):
        self.store.publish_batch(listening())
        self.store.publish_batch(metadata())
        favorites = FavoritesCoordinator(self.store, ACCOUNT, "favorites")
        favorites.begin_change("1" * 32, TRACK, True, DATE)
        favorites.resolve_change("1" * 32, True, DATE, resolution="confirmed", remote_settled=True)
        before = Snapshot.load(self.store, ACCOUNT)
        self.assertEqual(before.pending_batches(), 3)
        result = consolidate(self.store, ACCOUNT)
        self.assertEqual((result.saved_events, result.saved_metadata, result.saved_favorites), (1, 1, 1))
        after = Snapshot.load(self.store, ACCOUNT)
        self.assertEqual(after.pending_batches(), 0)
        self.assertEqual(after.rows("track_audio")[0]["audio"], {"energy": 0})
        self.assertEqual(FavoriteQueryService(after).liked_ids(), {TRACK})
        self.assertEqual(before.rows("liked_tracks"), [])
        self.assertEqual(self.store.publish_batch(listening()).status, "already_saved")

    def test_remote_favorite_reservations_remain_serialized_and_recoverable(self):
        one = FavoritesCoordinator(self.store, ACCOUNT, "first")
        two = FavoritesCoordinator(GitHubGitStore("demo", "private-data", self.api), ACCOUNT, "second")
        one.begin_change("1" * 32, TRACK, True, DATE)
        self.assertEqual(two.pending_change()["request_id"], "1" * 32)
        with self.assertRaises(FavoriteOperationPending):
            two.begin_change("2" * 32, TRACK, False, DATE)
        one.resolve_change("1" * 32, False, DATE, resolution="reconciled", remote_settled=True)
        self.assertIsNone(two.pending_change())

    def test_rate_limit_auth_and_server_errors_are_explicit_and_not_retried(self):
        for status in (401, 403, 404, 429, 500):
            self.api.calls.clear()
            self.api.error = lambda method, path, body: GitHubResponse(
                status, {"Retry-After": "60", "X-RateLimit-Reset": "12345"}, {"secret": "must-not-appear"},
            )
            with self.assertRaises(GitHubHTTPError) as error:
                self.store.head()
            self.assertEqual(error.exception.status, status)
            self.assertEqual(error.exception.retry_after, "60")
            self.assertNotIn("must-not-appear", str(error.exception))
            self.assertEqual(len(self.api.calls), 1)

    def test_ref_validation_failure_without_race_is_not_misreported_as_conflict(self):
        self.api.error = lambda method, path, body: GitHubResponse(422, {}, None) if method == "PATCH" else None
        with self.assertRaises(GitHubHTTPError) as error:
            self.store.publish(self.store.head(), {"one.csv": b"one"}, "Rejected")
        self.assertEqual(error.exception.status, 422)

    def test_truncated_trees_bad_blob_encoding_and_checksums_fail_closed(self):
        commit = self.store.publish(self.store.head(), {"one.csv": b"one"}, "First")
        mutations = (
            ("/git/trees/", {"truncated": True}),
            ("/git/blobs/", {"encoding": "utf8"}),
            ("/git/blobs/", {"content": "%%%"}),
            ("/git/blobs/", {"content": base64.b64encode(b"two").decode()}),
            ("/git/blobs/", {"size": 200}),
        )
        for prefix, update in mutations:
            self.api.malformed = lambda method, path, result: {**result, **update} if path.startswith(prefix) else result
            fresh = GitHubGitStore("demo", "private-data", self.api)
            with self.subTest(update=update), self.assertRaises(StorageError):
                fresh.read(commit, "one.csv")
        self.api.malformed = None

    def test_created_blob_hash_is_verified_before_commit(self):
        self.api.malformed = lambda method, path, result: {"sha": "f" * 40} if (
            method == "POST" and path == "/git/blobs"
        ) else result
        before = self.api.head
        with self.assertRaises(StorageError):
            self.store.publish(before, {"one.csv": b"one"}, "Bad hash")
        self.assertEqual(self.api.head, before)

    def test_wrong_repository_identity_and_missing_branch_are_explicit(self):
        self.api.full_name = "demo/different"
        with self.assertRaises(StorageError):
            self.store.head()
        self.api.full_name = "demo/private-data"
        self.api.error = lambda method, path, body: GitHubResponse(404, {}, None) if "/git/ref/" in path else None
        with self.assertRaises(GitHubHTTPError):
            self.store.head()
        self.assertFalse(any(method == "POST" for method, _, _ in self.api.calls))

    def test_unsupported_and_duplicate_tree_entries_are_not_silently_dropped(self):
        commit = self.store.publish(self.store.head(), {"one.csv": b"one"}, "First")
        for mode in ("120000", "160000"):
            self.api.malformed = lambda method, path, result: {
                **result, "tree": [{**result["tree"][0], "mode": mode}],
            } if path.startswith("/git/trees/") else result
            with self.assertRaises(StorageError):
                GitHubGitStore("demo", "private-data", self.api).list_paths(commit)
        self.api.malformed = lambda method, path, result: {
            **result, "tree": result["tree"] * 2,
        } if path.startswith("/git/trees/") else result
        with self.assertRaises(StorageError):
            GitHubGitStore("demo", "private-data", self.api).list_paths(commit)

    def test_failed_object_creation_leaves_visible_branch_unchanged(self):
        before = self.api.head
        self.api.error = lambda method, path, body: GitHubResponse(500, {}, None) if (
            method == "POST" and path == "/git/commits"
        ) else None
        with self.assertRaises(GitHubHTTPError):
            self.store.publish(before, {"one.csv": b"one"}, "Failure")
        self.assertEqual(self.api.head, before)
        self.assertEqual(self.store.list_paths(before), [])

    def test_favorite_resolution_after_lost_remote_ack_is_idempotent(self):
        favorites = FavoritesCoordinator(self.store, ACCOUNT, "favorites")
        favorites.begin_change("1" * 32, TRACK, True, DATE)

        def uncertain():
            raise StorageError("Simulated lost resolution acknowledgment")

        self.api.after_patch = uncertain
        with self.assertRaises(StorageError):
            favorites.resolve_change("1" * 32, True, DATE, resolution="confirmed", remote_settled=True)
        recovered = favorites.resolve_change("1" * 32, True, DATE, resolution="confirmed", remote_settled=True)
        self.assertEqual(recovered.status, "already_saved")
        self.assertIsNone(favorites.pending_change())
        self.assertEqual(consolidate(self.store, ACCOUNT).saved_favorites, 1)

    def test_file_size_and_directory_replacement_rejected(self):
        with self.assertRaises(ValidationError):
            self.store.publish(self.api.head, {"huge.csv": b"x" * (20 * 1024 * 1024 + 1)}, "Too large")
        first = self.store.publish(self.api.head, {"folder/one.csv": b"one"}, "First")
        with self.assertRaises(ValidationError):
            self.store.publish(first, {"folder": b"invalid"}, "Replace directory")

    def test_exact_twenty_mib_file_limit_round_trips(self):
        data = b"x" * (20 * 1024 * 1024)
        commit = self.store.publish(self.store.head(), {"limit.csv": data}, "Exact limit")
        self.assertEqual(self.store.read(commit, "limit.csv"), data)

    def test_batch_conflicts_have_bounded_retries(self):
        def conflict(method, path, body):
            if method == "PATCH":
                self.api.concurrent(f"race-{len(self.api.commits)}.csv")
                return GitHubResponse(422, {}, None)
            return None

        self.api.error = conflict
        with self.assertRaises(PublicationConflict):
            self.store.publish_batch(listening(), attempts=2)
        self.assertEqual(sum(method == "PATCH" for method, _, _ in self.api.calls), 2)


class HTTPTransportTest(unittest.TestCase):
    def test_only_exact_plan_error_is_classified_without_retaining_body(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        session = MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        plan = b'{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature."}'
        for status, content, expected in (
            (403, plan, "private_rules_require_plan_upgrade"),
            (401, plan, None),
            (403, b'{"message":"Resource not accessible by personal access token"}', None),
            (403, b'{"message":"sensitive-provider-content"}', None),
            (403, b"not-json-sensitive", None),
            (403, b'{"message":null}', None),
        ):
            with self.subTest(status=status, expected=expected):
                response.status_code = status
                response.iter_content.return_value = [content]
                with patch("requests.Session", return_value=session):
                    result = RequestsGitHubTransport(lambda: "synthetic-token")(
                        "GET", "https://api.github.com/repos/demo/private-data/rules/branches/main", None,
                    )
                self.assertEqual(result.error_code, expected)
                self.assertIsNone(result.body)
                self.assertNotIn("sensitive", repr(result))

    def test_token_headers_timeouts_and_redirect_rejection(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        response.headers = {"Location": "https://elsewhere.invalid", "Retry-After": "20"}
        response.iter_content.return_value = [b"credential-containing-error"]
        session = MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        with patch("requests.Session", return_value=session):
            result = RequestsGitHubTransport(lambda: "synthetic-token")(
                "GET", "https://api.github.com/repos/demo/private-data", None,
            )
        self.assertEqual(result.status, 302)
        self.assertIsNone(result.body)
        kwargs = session.request.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer synthetic-token")
        self.assertFalse(session.trust_env)

    def test_request_exception_does_not_include_token_or_response(self):
        import requests

        session = MagicMock()
        session.__enter__.return_value = session
        session.request.side_effect = requests.Timeout("synthetic-sensitive-token")
        with patch("requests.Session", return_value=session):
            with self.assertRaises(StorageError) as error:
                RequestsGitHubTransport(lambda: "synthetic-sensitive-token")(
                    "POST", "https://api.github.com/repos/demo/private-data/git/blobs", {},
                )
        self.assertNotIn("synthetic-sensitive-token", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_invalid_token_and_arbitrary_endpoint_rejected(self):
        with self.assertRaises(StorageError):
            RequestsGitHubTransport(lambda: "")("GET", "https://api.github.com/repos/demo/private-data", None)
        with self.assertRaises(ValidationError):
            RequestsGitHubTransport(lambda: "token")("GET", "https://elsewhere.invalid", None)

    def test_json_success_and_malformed_response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.headers = {}
        response.iter_content.return_value = [b'{"private":true}']
        session = MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        transport = RequestsGitHubTransport(lambda: "token")
        with patch("requests.Session", return_value=session):
            self.assertEqual(transport("GET", "https://api.github.com/repos/demo/private-data", None).body, {"private": True})
            response.iter_content.return_value = [b'{"duplicate":1,"duplicate":2}']
            with self.assertRaises(StorageError):
                transport("GET", "https://api.github.com/repos/demo/private-data", None)

    def test_timeout_validation_and_response_size_limit(self):
        for timeout in (0, -1, True, float("nan"), float("inf")):
            with self.assertRaises(ValidationError):
                RequestsGitHubTransport(lambda: "token", timeout=timeout)
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [b"x" * (32 * 1024 * 1024 + 1)]
        session = MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        with patch("requests.Session", return_value=session), self.assertRaises(StorageError):
            RequestsGitHubTransport(lambda: "token")("GET", "https://api.github.com/repos/demo/private-data", None)
