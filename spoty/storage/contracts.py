from dataclasses import dataclass
import re
from typing import Protocol

from spoty.domain.favorites import FavoritesBatch
from spoty.domain.listening import ListeningBatch, ValidationError, validate_account
from spoty.domain.metadata import MetadataBatch
from spoty.storage.csv_codec import encode_batch


class StorageError(RuntimeError):
    """Persistence failed without exposing credentials or provider output."""


class PublicationConflict(StorageError):
    """The data branch changed since the caller read it."""


def validate_path(path: str) -> None:
    if (
        not isinstance(path, str) or not path or len(path) > 512
        or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) for part in path.split("/"))
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValidationError("Invalid repository-relative data path.")


def validate_commit(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValidationError("Expected a full SHA-1 commit ID.")


def commit_message(message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValidationError("A commit message is required.")
    return message + "\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>\n"


class DataStore(Protocol):
    def head(self) -> str: ...
    def list_paths(self, commit: str, prefix: str | None = None) -> list[str]: ...
    def read(self, commit: str, path: str) -> bytes: ...
    def publish(self, expected: str | None, changes: dict[str, bytes | None], message: str) -> str: ...


@dataclass(frozen=True)
class BatchPublication:
    status: str
    commit_id: str
    batch_id: str


def batch_path(batch: ListeningBatch | MetadataBatch | FavoritesBatch) -> str:
    revision = f"{batch.revision:020d}/" if isinstance(batch, FavoritesBatch) else ""
    return (
        f"accounts/{batch.account_key}/inbox/{batch.batch_kind}/"
        f"{revision}{batch.captured_at[:7]}/{batch.batch_id}.csv"
    )


def publish_batch(
    store: DataStore, batch: ListeningBatch | MetadataBatch | FavoritesBatch, *, attempts: int = 3,
) -> BatchPublication:
    if isinstance(batch, FavoritesBatch):
        raise ValidationError("Favorite captures require durable coordination.")
    if type(attempts) is not int or attempts < 1:
        raise ValidationError("Publication attempts must be positive.")
    validate_account(batch.account_key)
    data = encode_batch(batch)
    path = batch_path(batch)
    archive = path.replace("/inbox/", "/archive/", 1)
    quarantine = path.replace("/inbox/", "/quarantine/", 1)
    for _ in range(attempts):
        commit = store.head()
        paths = set(store.list_paths(commit, f"accounts/{batch.account_key}"))
        if quarantine in paths:
            raise StorageError("This batch was rejected; review its quarantine receipt.")
        for candidate in (path, archive):
            if candidate in paths:
                if store.read(commit, candidate) != data:
                    raise PublicationConflict("Batch identity has different stored content.")
                return BatchPublication("already_saved", commit, batch.batch_id)
        try:
            published = store.publish(commit, {path: data}, f"Publish {batch.batch_kind}")
            return BatchPublication("saved", published, batch.batch_id)
        except PublicationConflict:
            continue
    raise PublicationConflict("Batch publication exhausted conflict retries.")
