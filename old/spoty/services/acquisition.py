"""Bounded capture orchestration; injected clients own all remote operations.

Timestamps and request IDs belong to the caller, so persistence can be retried
without changing capture identity. Exceptions retain their original types.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from spoty.domain.favorites import request_id
from spoty.domain.listening import ListeningBatch, ValidationError, utc_text, validate_account
from spoty.domain.metadata import MetadataBatch
from spoty.services.favorites import (
    FavoriteCaptureRejected, FavoriteOperationPending, FavoriteReservation,
    FavoriteSnapshotPublication, FavoritesCoordinator,
)
from spoty.storage.contracts import BatchPublication, DataStore, publish_batch


class FavoriteStatusProtocol(Protocol):
    def are_tracks_liked(self, track_ids: list[str]) -> dict[str, bool]: ...


class SpotifyClientProtocol(FavoriteStatusProtocol, Protocol):
    def get_recently_played(self, limit: int = 50) -> dict[str, Any]: ...
    def get_liked_songs(self, limit: int = 50, offset: int = 0) -> dict[str, Any]: ...
    def like_track(self, track_id: str) -> bool: ...
    def unlike_track(self, track_id: str) -> bool: ...


class FavoriteActionUnconfirmed(FavoriteOperationPending):
    """A reserved favorite intent needs observation because the mutation was not confirmed."""


@dataclass(frozen=True)
class FavoritesAcquisitionResult:
    reservation: FavoriteReservation
    publication: FavoriteSnapshotPublication | None
    pages: int

    @property
    def status(self) -> str:
        return self.publication.status if self.publication else self.reservation.status


@dataclass(frozen=True)
class FavoriteChangeResult:
    reservation: FavoriteReservation
    publication: BatchPublication | None

    @property
    def status(self) -> str:
        return self.publication.status if self.publication else self.reservation.status


@dataclass(frozen=True)
class SmarterPlaylistsAcquisitionResult:
    publication: BatchPublication | None
    missing_ids: tuple[str, ...]


class CsvAcquisitionService:
    """Capture into one account's inbox without consolidation or SQL access.

    No client is constructed implicitly. A provider callable must return already
    normalized enrichment records, not a legacy service that writes to SQL.
    """

    def __init__(
        self, store: DataStore, account: str, producer: str, *,
        spotify: SpotifyClientProtocol | None = None,
    ):
        validate_account(account)
        if not isinstance(producer, str) or not producer:
            raise ValidationError("A capture producer is required.")
        self.store, self.account, self.producer = store, account, producer
        self.spotify = spotify
        self.favorites = FavoritesCoordinator(store, account, producer)

    def _spotify(self) -> SpotifyClientProtocol:
        if self.spotify is None:
            raise ValidationError("An injected Spotify client is required.")
        return self.spotify

    @staticmethod
    def _limit(limit: int) -> None:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValidationError("Spotify capture limits must be between 1 and 50.")

    def publish_recent_listening(
        self, response: dict[str, Any], captured_at: str,
    ) -> BatchPublication | None:
        utc_text(captured_at)
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise ValidationError("Expected a recent listening response with items.")
        if not response["items"]:
            return None
        batch = ListeningBatch.from_items(
            self.account, self.producer, captured_at, response["items"],
        )
        return publish_batch(self.store, batch)

    def acquire_recent_listening(
        self, captured_at: str, *, limit: int = 50,
    ) -> BatchPublication | None:
        self._limit(limit)
        utc_text(captured_at)
        return self.publish_recent_listening(
            self._spotify().get_recently_played(limit=limit), captured_at,
        )

    def publish_enrichment(
        self, records: list[dict[str, Any]], fetched_at: str,
    ) -> BatchPublication | None:
        utc_text(fetched_at)
        if not isinstance(records, list):
            raise ValidationError("Expected normalized enrichment records.")
        if not records:
            return None
        return publish_batch(self.store, MetadataBatch.from_enrichment_records(
            self.account, self.producer, fetched_at, records,
        ))

    def acquire_enrichment(
        self, fetch_records: Callable[[], list[dict[str, Any]]], fetched_at: str,
    ) -> BatchPublication | None:
        """Invoke one caller-bounded, normalized provider acquisition."""
        utc_text(fetched_at)
        return self.publish_enrichment(fetch_records(), fetched_at)

    def publish_smarterplaylists(
        self, captures: list[dict[str, Any]], captured_at: str, *,
        live_track: bool = False,
    ) -> BatchPublication | None:
        utc_text(captured_at)
        if not isinstance(captures, list):
            raise ValidationError("Expected normalized SmarterPlaylists captures.")
        if not captures:
            return None
        return publish_batch(self.store, MetadataBatch.from_smarterplaylists(
            self.account, self.producer, captured_at, captures, live_track=live_track,
        ))

    def acquire_smarterplaylists(
        self, client: Any, entity: str, candidates: list[dict[str, Any]],
        captured_at: str, *, live_track: bool = False,
    ) -> SmarterPlaylistsAcquisitionResult:
        """Use the existing adapter's single capture call, including missing IDs."""
        utc_text(captured_at)
        captures, missing_ids = client.capture(entity, candidates)
        publication = self.publish_smarterplaylists(
            captures, captured_at, live_track=live_track,
        )
        return SmarterPlaylistsAcquisitionResult(publication, tuple(missing_ids))

    def acquire_favorites_snapshot(
        self, sync_id: str, started_at: str, completed_at: Callable[[], str], *,
        limit: int = 50,
    ) -> FavoritesAcquisitionResult:
        """Reserve, traverse once, then submit only the observed pagination.

        A remote exception leaves the durable reservation untouched. Retrying
        that request starts at zero; a previously finalized request never
        dispatches another download. There are no network retries or guessed
        terminal pages, including when Spotify returns an empty page.
        """
        self._limit(limit)
        spotify = self._spotify()
        reservation = self.favorites.reserve_snapshot(sync_id, started_at)
        if reservation.status == "already_invalid":
            raise FavoriteCaptureRejected("Previously rejected favorite capture remains preserved.")
        if reservation.status not in ("reserved", "already_reserved"):
            return FavoritesAcquisitionResult(reservation, None, 0)
        pages = []
        offset = 0
        total = None
        seen = set()
        while True:
            page = spotify.get_liked_songs(limit=limit, offset=offset)
            pages.append(page)
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                break
            items = page["items"]
            page_total = page.get("total")
            page_limit = page.get("limit")
            if (
                type(page.get("offset")) is not int or page["offset"] != offset
                or type(page_total) is not int or page_total < 0
                or type(page_limit) is not int or not 1 <= page_limit <= 50
                or len(items) > page_limit
                or (total is not None and total != page_total)
            ):
                break
            total = page_total
            invalid_item = False
            for item in items:
                track = item.get("track") if isinstance(item, dict) else None
                track_id = track.get("id") if isinstance(track, dict) else None
                if not isinstance(track_id, str) or track_id in seen:
                    invalid_item = True
                    break
                seen.add(track_id)
            offset += len(items)
            if (
                invalid_item or offset >= total or not items
                or not isinstance(page.get("next"), str) or not page["next"]
            ):
                break
        # The coordinator validates the whole traversal and durably rejects
        # malformed captures without publishing a replacement favorite revision.
        publication = self.favorites.submit_snapshot(sync_id, completed_at(), pages)
        return FavoritesAcquisitionResult(reservation, publication, len(pages))

    def change_favorite(
        self, operation_id: str, track_id: str, desired_liked: bool,
        started_at: str, completed_at: Callable[[], str],
    ) -> FavoriteChangeResult:
        """Journal an intent, dispatch at most once, then publish its confirmed result.

        If the remote call or local publication fails, the durable pending
        operation blocks replay. Resolve it only through explicit observation.
        """
        reservation = self.favorites.begin_change(
            operation_id, track_id, desired_liked, started_at,
        )
        if reservation.status != "reserved":
            return FavoriteChangeResult(reservation, None)

        spotify = self._spotify()
        succeeded = (
            spotify.like_track(track_id)
            if desired_liked
            else spotify.unlike_track(track_id)
        )
        if succeeded is not True:
            raise FavoriteActionUnconfirmed(
                "Spotify did not confirm the favorite change; observe the settled state before retrying."
            )
        publication = self.favorites.resolve_change(
            operation_id, desired_liked, completed_at(),
            resolution="confirmed", remote_settled=True,
        )
        return FavoriteChangeResult(reservation, publication)

    def recover_pending_favorite(
        self, observed_at: Callable[[], str], *, remote_settled: bool = False,
        expected_request_id: str | None = None,
        favorite_status_client: FavoriteStatusProtocol | None = None,
    ) -> BatchPublication | None:
        """Observe a settled pending request; never replay a like or unlike.

        A contains observation cannot prove an earlier in-flight mutation has
        stopped. The caller must establish that separately before recovery.
        When supplied, the request ID must still match the operation displayed
        to the user before any provider observation is made.
        """
        if expected_request_id is not None:
            request_id(expected_request_id)
        pending = self.favorites.pending_change()
        if pending is None:
            return None
        if (
            expected_request_id is not None
            and pending["request_id"] != expected_request_id
        ):
            raise FavoriteOperationPending(
                "The pending favorite operation changed; review its current status before recovery."
            )
        if remote_settled is not True:
            raise ValidationError("Establish that the remote request settled before recovery.")
        track_id = pending["track_id"]
        observer = (
            favorite_status_client
            if favorite_status_client is not None
            else self._spotify()
        )
        response = observer.are_tracks_liked([track_id])
        if (
            not isinstance(response, dict) or set(response) != {track_id}
            or type(response[track_id]) is not bool
        ):
            raise ValidationError("Expected one boolean favorite observation.")
        return self.favorites.resolve_change(
            pending["request_id"], response[track_id], observed_at(),
            resolution="reconciled", remote_settled=True,
        )
