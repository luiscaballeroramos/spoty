import base64
import binascii
import hashlib
import re
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from spoty.domain.favorites import FavoritesBatch
from spoty.domain.listening import ListeningBatch, ValidationError, parse_json
from spoty.domain.metadata import MetadataBatch
from spoty.storage.contracts import (
    BatchPublication, PublicationConflict, StorageError,
    commit_message, publish_batch, validate_commit, validate_path,
)
from spoty.storage.csv_codec import MAX_CSV_BYTES


@dataclass(frozen=True)
class GitHubResponse:
    status: int
    headers: dict[str, str]
    body: Any
    error_code: str | None = None


class GitHubTransport(Protocol):
    def __call__(self, method: str, url: str, body: dict[str, Any] | None) -> GitHubResponse: ...


class GitHubHTTPError(StorageError):
    def __init__(self, status: int, retry_after: str | None = None, reset_at: str | None = None):
        self.status, self.retry_after, self.reset_at = status, retry_after, reset_at
        super().__init__(f"GitHub data request failed (HTTP {status}).")


class RequestsGitHubTransport:
    """Token supplied at call time, never persisted in data, URLs or exception text."""

    def __init__(self, token: Callable[[], str], *, timeout: float = 30):
        if not callable(token) or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValidationError("GitHub needs a token provider and bounded timeout.")
        self._token, self.timeout = token, timeout

    def __call__(self, method, url, body):
        import requests

        if not url.startswith("https://api.github.com/repos/") or method not in ("GET", "POST", "PATCH"):
            raise ValidationError("Unsupported GitHub transport request.")
        token = self._token()
        if not isinstance(token, str) or not token or any(char.isspace() for char in token):
            raise StorageError("GitHub credential provider returned an invalid token.")
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(
                    method, url, json=body, timeout=self.timeout, allow_redirects=False, stream=True,
                    headers={
                        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "spoty-csv-git",
                    },
                ) as response:
                    # Bound base64 blob responses and recursive tree listings.
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > 32 * 1024 * 1024:
                            raise StorageError("GitHub response exceeds the transport limit.")
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    if response.status_code >= 300:
                        error_code = None
                        if response.status_code == 403:
                            try:
                                failure = parse_json(content.decode("utf-8"))
                            except (UnicodeError, ValidationError):
                                failure = None
                            if isinstance(failure, dict) and failure.get("message") == (
                                "Upgrade to GitHub Pro or make this repository public to enable this feature."
                            ):
                                error_code = "private_rules_require_plan_upgrade"
                        return GitHubResponse(response.status_code, headers, None, error_code)
                    try:
                        payload = parse_json(content.decode("utf-8"))
                    except (UnicodeError, ValidationError):
                        raise StorageError("GitHub returned invalid JSON.") from None
                    return GitHubResponse(response.status_code, headers, payload)
        except requests.RequestException:
            raise StorageError("GitHub transport failed; publication outcome may be uncertain.") from None


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


class GitHubGitStore:
    """Existing private GitHub data branch, with immutable object reads and no force push."""

    def __init__(self, owner: str, repository: str, transport: GitHubTransport, *, branch: str = "main"):
        for value in (owner, repository, branch):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", value):
                raise ValidationError("Invalid GitHub repository or branch identifier.")
        if (owner.lower(), repository.lower()) == ("luiscaballeroramos", "spoty"):
            raise ValidationError("The application repository cannot be used as a data repository.")
        self.owner, self.repository, self.branch = owner, repository, branch
        self._transport = transport
        self._base = f"https://api.github.com/repos/{owner}/{repository}"
        self._trees: dict[str, list[dict[str, Any]]] = {}
        self._roots: dict[str, str] = {}

    def _request(self, method, path, body=None):
        response = self._transport(method, self._base + path, body)
        if response.status not in (200, 201):
            headers = {key.lower(): value for key, value in response.headers.items()}
            raise GitHubHTTPError(
                response.status, headers.get("retry-after"), headers.get("x-ratelimit-reset"),
            )
        if not isinstance(response.body, dict):
            raise StorageError("GitHub returned an invalid object.")
        return response.body

    def _sha(self, response):
        value = response.get("sha")
        try:
            validate_commit(value)
        except ValidationError:
            raise StorageError("GitHub returned an invalid object identity.") from None
        return value

    def _private(self):
        repository = self._request("GET", "")
        if (
            repository.get("private") is not True or repository.get("archived") is not False
            or str(repository.get("full_name", "")).lower() != f"{self.owner}/{self.repository}".lower()
        ):
            raise StorageError("An existing, non-archived private data repository is required.")

    def head(self):
        self._private()
        response = self._request("GET", f"/git/ref/heads/{self.branch}")
        if response.get("ref") != f"refs/heads/{self.branch}" or not isinstance(response.get("object"), dict):
            raise StorageError("GitHub returned an unexpected branch reference.")
        if response["object"].get("type") != "commit":
            raise StorageError("The data branch must point to a commit.")
        return self._sha(response["object"])

    def _root(self, commit):
        validate_commit(commit)
        if commit not in self._roots:
            response = self._request("GET", f"/git/commits/{commit}")
            if self._sha(response) != commit or not isinstance(response.get("tree"), dict):
                raise StorageError("GitHub returned an unexpected commit.")
            self._roots[commit] = self._sha(response["tree"])
        return self._roots[commit]

    def _tree(self, sha):
        if sha not in self._trees:
            response = self._request("GET", f"/git/trees/{sha}")
            if self._sha(response) != sha or response.get("truncated") is not False:
                raise StorageError("Incomplete GitHub tree; refusing partial data.")
            entries = response.get("tree")
            if not isinstance(entries, list):
                raise StorageError("GitHub returned an invalid tree.")
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise StorageError("GitHub returned an invalid tree entry.")
                path = entry.get("path")
                validate_path(path)
                if "/" in path or path in seen:
                    raise StorageError("Invalid or duplicated immediate GitHub tree path.")
                seen.add(path)
                self._sha(entry)
                if (entry.get("type"), entry.get("mode")) not in (
                    ("tree", "040000"), ("blob", "100644"),
                ):
                    raise StorageError("Data repositories must contain plain files and directories only.")
            self._trees[sha] = entries
        return self._trees[sha]

    def _entry(self, commit, path):
        validate_path(path)
        sha = self._root(commit)
        parts = path.split("/")
        for index, part in enumerate(parts):
            entry = next((entry for entry in self._tree(sha) if entry["path"] == part), None)
            if entry is None:
                return None
            if index == len(parts) - 1:
                return entry
            if entry["type"] != "tree":
                return None
            sha = entry["sha"]
        return None

    def list_paths(self, commit, prefix=None):
        sha = self._root(commit)
        root_path = ""
        if prefix is not None:
            entry = self._entry(commit, prefix)
            if entry is None:
                return []
            if entry["type"] == "blob":
                return [prefix]
            sha, root_path = entry["sha"], prefix + "/"
        result = []
        pending = [(sha, root_path, 0)]
        traversed = 0
        while pending:
            traversed += 1
            current, path, depth = pending.pop()
            if depth > 32 or traversed > 100000:
                raise StorageError("GitHub data tree exceeds traversal limits.")
            for entry in self._tree(current):
                full_path = path + entry["path"]
                validate_path(full_path)
                if entry["type"] == "tree":
                    pending.append((entry["sha"], full_path + "/", depth + 1))
                else:
                    result.append(full_path)
                    if len(result) > 100000:
                        raise StorageError("GitHub data tree exceeds file count limit.")
        return sorted(result)

    def read(self, commit, path):
        entry = self._entry(commit, path)
        if entry is None or entry["type"] != "blob":
            raise StorageError("Requested data file does not exist at the pinned commit.")
        response = self._request("GET", f"/git/blobs/{entry['sha']}")
        if (
            self._sha(response) != entry["sha"] or response.get("encoding") != "base64"
            or not isinstance(response.get("content"), str)
            or type(response.get("size")) is not int or not 0 <= response["size"] <= MAX_CSV_BYTES
        ):
            raise StorageError("Invalid GitHub data blob.")
        try:
            data = base64.b64decode(response["content"].replace("\n", ""), validate=True)
        except (ValueError, binascii.Error):
            raise StorageError("Invalid GitHub blob encoding.") from None
        if len(data) != response["size"] or blob_sha(data) != entry["sha"]:
            raise StorageError("GitHub blob checksum or size mismatch.")
        return data

    def publish(self, expected, changes, message):
        if expected is None:
            raise ValidationError("Initialize the private data branch before using GitHub transport.")
        validate_commit(expected)
        message = commit_message(message)
        for path, data in changes.items():
            validate_path(path)
            if data is not None and (not isinstance(data, bytes) or len(data) > MAX_CSV_BYTES):
                raise ValidationError("Published data must be bytes within the file size limit.")
        if self.head() != expected:
            raise PublicationConflict("GitHub data branch changed before publication.")
        entries = []
        for path in sorted(changes):
            data = changes[path]
            old = self._entry(expected, path)
            if old is not None and old["type"] != "blob":
                raise ValidationError("Cannot replace a data directory with a file.")
            if data is None:
                if old is None:
                    continue
                sha = None
            else:
                sha = blob_sha(data)
                if old is not None and old["sha"] == sha:
                    continue
                response = self._request("POST", "/git/blobs", {
                    "content": base64.b64encode(data).decode("ascii"), "encoding": "base64",
                })
                if self._sha(response) != sha:
                    raise StorageError("Created GitHub blob disagrees with published content.")
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
        if not entries:
            if self.head() != expected:
                raise PublicationConflict("GitHub data branch changed before no-op publication.")
            return expected
        tree = self._sha(self._request("POST", "/git/trees", {
            "base_tree": self._root(expected), "tree": entries,
        }))
        commit = self._sha(self._request("POST", "/git/commits", {
            "tree": tree, "parents": [expected],
            "message": message,
        }))
        if self.head() != expected:
            raise PublicationConflict("GitHub data branch changed before reference update.")
        try:
            reference = self._request("PATCH", f"/git/refs/heads/{self.branch}", {"sha": commit, "force": False})
        except GitHubHTTPError as error:
            if error.status in (409, 422) and self.head() != expected:
                raise PublicationConflict("GitHub refused a concurrent branch update.") from None
            raise
        if (
            reference.get("ref") != f"refs/heads/{self.branch}"
            or not isinstance(reference.get("object"), dict) or self._sha(reference["object"]) != commit
        ):
            raise StorageError("GitHub reference update outcome is uncertain; retry the same operation.")
        return commit

    def publish_batch(self, batch: ListeningBatch | MetadataBatch | FavoritesBatch, *, attempts: int = 3) -> BatchPublication:
        return publish_batch(self, batch, attempts=attempts)
