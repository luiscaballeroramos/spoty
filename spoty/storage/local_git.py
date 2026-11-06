import os
import subprocess
import tempfile
from pathlib import Path

from spoty.domain.listening import ListeningBatch, ValidationError
from spoty.domain.metadata import MetadataBatch
from spoty.domain.favorites import FavoritesBatch
from spoty.storage.contracts import (
    BatchPublication, PublicationConflict, StorageError, batch_path,
    commit_message, publish_batch, validate_commit as _commit_id, validate_path,
)


class LocalGitStore:
    """Explicitly initialized bare data repositories, never the application worktree."""

    REF = "refs/heads/main"

    def __init__(self, path: Path):
        self.path = path.resolve()
        if self._git("rev-parse", "--is-bare-repository").strip() != b"true":
            raise StorageError("A bare data repository is required.")
        if self._git("rev-parse", "--show-object-format").strip() != b"sha1":
            raise StorageError("Prototype data repositories require SHA-1 object format.")

    @classmethod
    def initialize(cls, path: Path):
        path = path.resolve()
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise StorageError("Initialization requires a new or empty directory.")
        path.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["git", "init", "--bare", "--object-format=sha1", "--initial-branch=main", str(path)],
                capture_output=True, env=cls._environment(),
            )
        except OSError as error:
            raise StorageError("Could not execute Git.") from error
        if result.returncode:
            raise StorageError("Could not initialize the data repository.")
        store = cls(path)
        store.publish(None, {}, "Initialize CSV data repository")
        return store

    @staticmethod
    def _environment() -> dict[str, str]:
        environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        environment.update({
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_PAGER": "cat",
            "GIT_AUTHOR_NAME": "Spoty prototype", "GIT_AUTHOR_EMAIL": "prototype@example.invalid",
            "GIT_COMMITTER_NAME": "Spoty prototype", "GIT_COMMITTER_EMAIL": "prototype@example.invalid",
        })
        return environment

    def _git(self, *arguments: str, data: bytes | None = None, index: str | None = None) -> bytes:
        environment = self._environment()
        if index is not None:
            environment["GIT_INDEX_FILE"] = index
        try:
            result = subprocess.run(
                ["git", "--no-pager", "--git-dir", str(self.path), *arguments],
                input=data, capture_output=True, env=environment,
            )
        except OSError as error:
            raise StorageError("Could not execute Git.") from error
        if result.returncode:
            raise StorageError(f"Git operation failed: {arguments[0]}.")
        return result.stdout

    def head(self) -> str:
        return self._git("rev-parse", "--verify", self.REF).decode("ascii").strip()

    def list_paths(self, commit: str, prefix: str | None = None) -> list[str]:
        _commit_id(commit)
        if prefix is not None:
            validate_path(prefix)
        arguments = ("ls-tree", "-r", "--name-only", "-z", commit)
        output = self._git(*arguments, *(("--", prefix) if prefix else ()))
        try:
            paths = [path.decode("ascii") for path in output.split(b"\0") if path]
        except UnicodeError as error:
            raise StorageError("Unexpected data repository path encoding.") from error
        for path in paths:
            validate_path(path)
        return paths

    def read(self, commit: str, path: str) -> bytes:
        _commit_id(commit)
        validate_path(path)
        return self._git("show", f"{commit}:{path}")

    def publish(
        self, expected: str | None, changes: dict[str, bytes | None], message: str,
    ) -> str:
        if expected is not None:
            _commit_id(expected)
        for path, data in changes.items():
            validate_path(path)
            if data is not None and not isinstance(data, bytes):
                raise ValidationError("Published content must be bytes.")
        with tempfile.TemporaryDirectory(prefix="spoty-index-") as temporary:
            index = str(Path(temporary) / "index")
            self._git("read-tree", expected or "--empty", index=index)
            updates = []
            for path, data in sorted(changes.items()):
                if data is None:
                    updates.append(f"0 {'0' * 40}\t{path}\n")
                else:
                    blob = self._git("hash-object", "-w", "--stdin", data=data).decode("ascii").strip()
                    updates.append(f"100644 {blob}\t{path}\n")
            if updates:
                self._git("update-index", "--index-info", data="".join(updates).encode("ascii"), index=index)
            tree = self._git("write-tree", index=index).decode("ascii").strip()
            if expected is not None:
                previous = self._git("rev-parse", f"{expected}^{{tree}}").decode("ascii").strip()
                if previous == tree:
                    if self.head() != expected:
                        raise PublicationConflict("Data branch changed before publication.")
                    return expected
            parents = ("-p", expected) if expected else ()
            commit = self._git("commit-tree", tree, *parents, data=commit_message(message).encode("utf-8"))
            commit_id = commit.decode("ascii").strip()
            try:
                self._git("update-ref", self.REF, commit_id, expected or "0" * 40)
            except StorageError as error:
                if self.head() != expected:
                    raise PublicationConflict("Data branch changed before publication.") from error
                raise
            return commit_id

    def publish_batch(
        self, batch: ListeningBatch | MetadataBatch | FavoritesBatch, *, attempts: int = 3,
    ) -> BatchPublication:
        return publish_batch(self, batch, attempts=attempts)
