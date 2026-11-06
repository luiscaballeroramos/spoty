import hashlib
from dataclasses import dataclass
from typing import Any

from spoty.domain.favorites import FavoritesBatch, counter, request_id, snapshot_ids, spotify_id
from spoty.domain.listening import (
    ValidationError, canonical_json, parse_json, reject_secrets,
    utc_text, utc_timestamp, validate_account,
)
from spoty.storage.csv_codec import MAX_CSV_BYTES, Field, Schema, decode_csv, encode_batch, encode_csv
from spoty.storage.contracts import (
    BatchPublication, DataStore, PublicationConflict, StorageError, batch_path,
)
from spoty.storage.snapshots import Snapshot


STATE_SCHEMA = Schema((
    Field("generation", "int"), Field("revision", "int"), Field("pending_request_id", nullable=True),
), ())
CHANGE_SCHEMA = Schema((
    Field("request_id"), Field("producer"), Field("track_id"), Field("desired_liked", "int"),
    Field("started_at"), Field("status"), Field("observed_liked", "int", True),
    Field("observed_at", nullable=True), Field("resolution", nullable=True),
    Field("batch_id", nullable=True),
), ("request_id",))
SYNC_SCHEMA = Schema((
    Field("request_id"), Field("producer"), Field("generation", "int"), Field("base_revision", "int"),
    Field("started_at"), Field("status"), Field("completed_at", nullable=True),
    Field("pages", "json", True), Field("batch_id", nullable=True), Field("error_code", nullable=True),
), ("request_id",))
REVISION_SCHEMA = Schema((
    Field("revision", "int"), Field("request_id"), Field("batch_id"), Field("path"), Field("sha256"),
), ("revision",))


class FavoriteOperationPending(StorageError):
    """A previous remote operation must be settled before another may start."""


class FavoriteCaptureRejected(ValidationError):
    """The invalid full capture was preserved but cannot replace the replica."""


class FavoriteRevisionBlocked(StorageError):
    """Do not skip a corrupt or missing favorite revision and invent a replica."""


@dataclass(frozen=True)
class FavoriteReservation:
    request_id: str
    status: str
    generation: int
    base_revision: int


@dataclass(frozen=True)
class FavoriteSnapshotPublication:
    status: str
    commit_id: str
    batch_id: str | None


def revision_path(account: str, revision: int) -> str:
    counter(revision, minimum=1)
    return f"accounts/{account}/coordination/favorites/revisions/{revision:020d}.csv"


def _one(store: DataStore, commit: str, path: str, schema: Schema) -> dict[str, Any]:
    rows = decode_csv(schema, store.read(commit, path))
    if len(rows) != 1:
        raise ValidationError("Favorites coordination requires exactly one record.")
    return rows[0]


def verify_revision(store: DataStore, commit: str, batch: FavoritesBatch, data: bytes) -> None:
    path = revision_path(batch.account_key, batch.revision)
    if path not in store.list_paths(commit, path):
        raise ValidationError("Favorite batch has no coordinated revision.")
    reference = _one(store, commit, path, REVISION_SCHEMA)
    if reference != {
        "revision": batch.revision, "request_id": batch.payloads[0]["request_id"],
        "batch_id": batch.batch_id, "path": batch_path(batch),
        "sha256": hashlib.sha256(data).hexdigest(),
    }:
        raise ValidationError("Favorite batch disagrees with its coordinated revision.")


def validate_pending_revisions(
    store: DataStore, commit: str, account: str,
    replica_rows: list[dict[str, Any]], paths: set[str],
) -> None:
    prefix = f"accounts/{account}/coordination/favorites"
    state_path = f"{prefix}/state.csv"
    applied = 0
    if replica_rows:
        if len(replica_rows) != 1 or replica_rows[0]["singleton_id"] != 1:
            raise FavoriteRevisionBlocked("Invalid favorites replica coordination state.")
        applied = replica_rows[0]["applied_revision"]
        counter(applied)
    if state_path not in paths:
        if applied or any(path.startswith(f"{prefix}/") for path in paths):
            raise FavoriteRevisionBlocked("Favorites coordination state is missing.")
        return
    state = _one(store, commit, state_path, STATE_SCHEMA)
    counter(state["revision"])
    if state["revision"] < applied:
        raise FavoriteRevisionBlocked("Favorites replica is ahead of its coordinated history.")
    last_applied = f"{prefix}/revisions/{applied:020d}.csv"
    references = sorted(
        path for path in paths
        if path.startswith(f"{prefix}/revisions/") and path > last_applied
    )
    if len(references) != state["revision"] - applied:
        raise FavoriteRevisionBlocked("Favorite revision references are missing or inconsistent.")
    for revision, path in enumerate(references, applied + 1):
        reference = _one(store, commit, path, REVISION_SCHEMA)
        if (
            path != revision_path(account, revision) or reference["revision"] != revision
            or reference["path"] not in paths
            or not reference["path"].startswith(f"accounts/{account}/inbox/favorites_capture/")
        ):
            raise FavoriteRevisionBlocked("A pending favorite revision lost its durable inbox capture.")


def apply_favorites(batch: FavoritesBatch, maps: dict[str, dict[tuple, dict[str, Any]]]):
    replica = maps["favorites_replica_state"]
    if replica and (set(replica) != {(1,)} or replica[(1,)]["singleton_id"] != 1):
        raise ValidationError("Invalid favorites replica state.")
    applied = replica[(1,)]["applied_revision"] if replica else 0
    counter(applied)
    if batch.revision != applied + 1:
        raise ValidationError("Favorites revisions must be consolidated without gaps.")
    existing = set(key[0] for key in maps["liked_tracks"])
    payload = batch.payloads[0]
    if payload["operation"] == "snapshot":
        desired = set(snapshot_ids(payload["pages"]))
    else:
        desired = set(existing)
        if payload["liked"]:
            desired.add(payload["track_id"])
        else:
            desired.discard(payload["track_id"])
    staged = {
        **maps,
        "liked_tracks": {(track_id,): {"id": track_id} for track_id in desired},
        "favorites_replica_state": {(1,): {"singleton_id": 1, "applied_revision": batch.revision}},
    }
    return staged, len(desired - existing), len(existing - desired)


class FavoritesCoordinator:
    """Durable coordination only: no Spotify calls, credentials or timeout unlocks."""

    def __init__(self, store: DataStore, account: str, producer: str, *, attempts: int = 3):
        validate_account(account)
        if not isinstance(producer, str) or not producer or type(attempts) is not int or attempts < 1:
            raise ValidationError("Favorites need a producer and positive retry limit.")
        self.store, self.account, self.producer, self.attempts = store, account, producer, attempts
        self.prefix = f"accounts/{account}/coordination/favorites"
        self.state_path = f"{self.prefix}/state.csv"

    def pending_change(self) -> dict[str, Any] | None:
        commit, _, state = self._load()
        operation_id = state["pending_request_id"]
        if operation_id is None:
            return None
        return _one(self.store, commit, f"{self.prefix}/changes/{operation_id}.csv", CHANGE_SCHEMA)

    def _load(self):
        commit = self.store.head()
        paths = set(self.store.list_paths(commit, self.prefix))
        if self.state_path not in paths:
            if paths:
                raise ValidationError("Favorites coordination state is missing.")
            state = {"generation": 0, "revision": 0, "pending_request_id": None}
        else:
            state = _one(self.store, commit, self.state_path, STATE_SCHEMA)
            counter(state["generation"])
            counter(state["revision"])
            if state["pending_request_id"] is not None:
                request_id(state["pending_request_id"])
                pending_path = f"{self.prefix}/changes/{state['pending_request_id']}.csv"
                if pending_path not in paths or _one(
                    self.store, commit, pending_path, CHANGE_SCHEMA,
                )["status"] != "pending":
                    raise ValidationError("Pending favorite operation journal is missing.")
        return commit, paths, state

    def _publish(self, commit, state, changes, message):
        return self.store.publish(
            commit, {**changes, self.state_path: encode_csv(STATE_SCHEMA, [state])}, message,
        )

    def _capture_changes(self, batch: FavoritesBatch) -> dict[str, bytes]:
        data = encode_batch(batch)
        return {
            batch_path(batch): data,
            revision_path(self.account, batch.revision): encode_csv(REVISION_SCHEMA, [{
                "revision": batch.revision, "request_id": batch.payloads[0]["request_id"],
                "batch_id": batch.batch_id, "path": batch_path(batch),
                "sha256": hashlib.sha256(data).hexdigest(),
            }]),
        }

    def begin_change(
        self, operation_id: str, track_id: str, desired_liked: bool, started_at: str,
    ) -> FavoriteReservation:
        request_id(operation_id)
        spotify_id(track_id)
        if type(desired_liked) is not bool:
            raise ValidationError("Favorite intent must be boolean.")
        started_at = utc_text(started_at)
        path = f"{self.prefix}/changes/{operation_id}.csv"
        for _ in range(self.attempts):
            commit, paths, state = self._load()
            if path in paths:
                previous = _one(self.store, commit, path, CHANGE_SCHEMA)
                if previous["track_id"] != track_id or previous["desired_liked"] != int(desired_liked):
                    raise PublicationConflict("Favorite request ID was reused with a different intent.")
                if previous["status"] not in ("pending", "resolved"):
                    raise ValidationError("Invalid favorite operation status.")
                return FavoriteReservation(
                    operation_id, f"already_{previous['status']}", state["generation"], state["revision"],
                )
            if state["pending_request_id"] is not None:
                raise FavoriteOperationPending("Settle the pending favorite operation first.")
            state["pending_request_id"] = operation_id
            row = {
                "request_id": operation_id, "producer": self.producer, "track_id": track_id,
                "desired_liked": int(desired_liked), "started_at": started_at, "status": "pending",
                "observed_liked": None, "observed_at": None, "resolution": None, "batch_id": None,
            }
            try:
                self._publish(commit, state, {path: encode_csv(CHANGE_SCHEMA, [row])}, "Reserve favorite change")
            except PublicationConflict:
                continue
            return FavoriteReservation(operation_id, "reserved", state["generation"], state["revision"])
        raise PublicationConflict("Favorite change reservation exhausted conflict retries.")

    def resolve_change(
        self, operation_id: str, observed_liked: bool, observed_at: str, *,
        resolution: str, remote_settled: bool,
    ) -> BatchPublication:
        request_id(operation_id)
        if (
            type(observed_liked) is not bool or resolution not in ("confirmed", "reconciled")
            or remote_settled is not True
        ):
            raise ValidationError("Resolve only after the remote request has settled and its state is known.")
        observed_at = utc_text(observed_at)
        path = f"{self.prefix}/changes/{operation_id}.csv"
        for _ in range(self.attempts):
            commit, paths, state = self._load()
            if path not in paths:
                raise ValidationError("Favorite change was not reserved.")
            row = _one(self.store, commit, path, CHANGE_SCHEMA)
            if row["status"] == "resolved":
                if row["observed_liked"] != int(observed_liked):
                    raise PublicationConflict("Resolved favorite request has a different observation.")
                return BatchPublication("already_saved", commit, row["batch_id"])
            if row["status"] != "pending" or state["pending_request_id"] != operation_id:
                raise ValidationError("Favorite request is not the active pending operation.")
            revision = state["revision"] + 1
            batch = FavoritesBatch(self.account, row["producer"], observed_at, ({
                "revision": revision, "request_id": operation_id, "operation": "change",
                "started_at": row["started_at"], "track_id": row["track_id"],
                "desired_liked": bool(row["desired_liked"]), "liked": observed_liked,
                "resolution": resolution,
            },))
            row.update({
                "status": "resolved", "observed_liked": int(observed_liked),
                "observed_at": observed_at, "resolution": resolution, "batch_id": batch.batch_id,
            })
            state.update({"revision": revision, "pending_request_id": None})
            try:
                published = self._publish(
                    commit, state, {
                        **self._capture_changes(batch), path: encode_csv(CHANGE_SCHEMA, [row]),
                    }, "Resolve favorite change",
                )
            except PublicationConflict:
                continue
            return BatchPublication("saved", published, batch.batch_id)
        raise PublicationConflict("Favorite resolution exhausted conflict retries.")

    def reserve_snapshot(self, sync_id: str, started_at: str) -> FavoriteReservation:
        request_id(sync_id)
        started_at = utc_text(started_at)
        path = f"{self.prefix}/snapshots/{sync_id}.csv"
        for _ in range(self.attempts):
            commit, paths, state = self._load()
            if path in paths:
                row = _one(self.store, commit, path, SYNC_SCHEMA)
                return FavoriteReservation(sync_id, f"already_{row['status']}", row["generation"], row["base_revision"])
            if state["pending_request_id"] is not None:
                raise FavoriteOperationPending("A favorite request is pending; do not start a full download.")
            generation = state["generation"] + 1
            counter(generation, minimum=1)
            state["generation"] = generation
            row = {
                "request_id": sync_id, "producer": self.producer, "generation": generation,
                "base_revision": state["revision"], "started_at": started_at, "status": "reserved",
                "completed_at": None, "pages": None, "batch_id": None, "error_code": None,
            }
            try:
                self._publish(commit, state, {path: encode_csv(SYNC_SCHEMA, [row])}, "Reserve favorite snapshot")
            except PublicationConflict:
                continue
            return FavoriteReservation(sync_id, "reserved", generation, state["revision"])
        raise PublicationConflict("Favorite snapshot reservation exhausted conflict retries.")

    def submit_snapshot(
        self, sync_id: str, completed_at: str, pages: list[dict[str, Any]],
    ) -> FavoriteSnapshotPublication:
        request_id(sync_id)
        completed_at = utc_text(completed_at)
        pages = parse_json(canonical_json(pages))
        reject_secrets(pages)
        path = f"{self.prefix}/snapshots/{sync_id}.csv"
        for _ in range(self.attempts):
            commit, paths, state = self._load()
            if path not in paths:
                raise ValidationError("Favorite snapshot was not reserved before acquisition.")
            row = _one(self.store, commit, path, SYNC_SCHEMA)
            if row["status"] != "reserved":
                if canonical_json(row["pages"]) != canonical_json(pages):
                    raise PublicationConflict("Favorite snapshot ID was reused with different pages.")
                if row["status"] == "invalid":
                    raise FavoriteCaptureRejected("Previously rejected favorite capture remains preserved.")
                if row["status"] not in ("published", "superseded"):
                    raise ValidationError("Invalid favorite snapshot reservation status.")
                return FavoriteSnapshotPublication(
                    "already_saved" if row["status"] == "published" else "superseded", commit, row["batch_id"],
                )
            if utc_timestamp(completed_at) < utc_timestamp(row["started_at"]):
                raise ValidationError("Favorite download ended before its reservation.")
            error_code = None
            batch = None
            try:
                batch = FavoritesBatch(self.account, row["producer"], completed_at, ({
                    "revision": row["base_revision"] + 1, "request_id": sync_id,
                    "operation": "snapshot", "started_at": row["started_at"],
                    "generation": row["generation"], "base_revision": row["base_revision"], "pages": pages,
                },))
            except ValidationError:
                error_code = "invalid_favorites_snapshot"
            superseded = (
                state["pending_request_id"] is not None or row["generation"] != state["generation"]
                or row["base_revision"] != state["revision"]
            )
            status = "invalid" if error_code else "superseded" if superseded else "published"
            row.update({
                "status": status, "completed_at": completed_at, "pages": pages,
                "error_code": error_code, "batch_id": batch.batch_id if status == "published" else None,
            })
            changes = {path: encode_csv(SYNC_SCHEMA, [row])}
            if len(changes[path]) > MAX_CSV_BYTES:
                raise ValidationError("Favorite capture exceeds the CSV size limit.")
            if status == "published" and batch is not None:
                changes.update(self._capture_changes(batch))
                state["revision"] = batch.revision
            try:
                published = self._publish(commit, state, changes, "Record favorite snapshot")
            except PublicationConflict:
                continue
            if error_code:
                raise FavoriteCaptureRejected("Invalid favorite capture preserved; replica unchanged.")
            return FavoriteSnapshotPublication(
                "saved" if status == "published" else "superseded", published, row["batch_id"],
            )
        raise PublicationConflict("Favorite snapshot publication exhausted conflict retries.")


class FavoriteQueryService:
    def __init__(self, snapshot: Snapshot):
        self.snapshot = snapshot

    def liked_ids(self) -> set[str]:
        ids = {row["id"] for row in self.snapshot.rows("liked_tracks")}
        for track_id in ids:
            spotify_id(track_id)
        return ids

    def is_liked(self, track_id: str) -> bool:
        spotify_id(track_id)
        return track_id in self.liked_ids()
