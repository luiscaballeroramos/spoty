import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from spoty.domain.favorites import request_id
from spoty.domain.listening import ValidationError
from spoty.services.acquisition import (
    CsvAcquisitionService, FavoriteStatusProtocol,
)
from spoty.services.favorites import FavoriteOperationPending, FavoritesCoordinator
from spoty.storage.contracts import BatchPublication, DataStore


class FavoriteRecoveryCallback(Protocol):
    def __call__(
        self, expected_request_id: str, *, remote_settled: bool,
    ) -> BatchPublication | None: ...


class SpotifyFavoriteStatusAdapter:
    """Expose only the favorite-state read operation needed by recovery."""

    def __init__(self, client: FavoriteStatusProtocol):
        self._client = client

    def are_tracks_liked(self, track_ids: list[str]) -> dict[str, bool]:
        return self._client.are_tracks_liked(track_ids)


def _authenticated_spotify_client() -> FavoriteStatusProtocol:
    import streamlit as st
    from streamlit.errors import StreamlitSecretNotFoundError

    try:
        secrets = st.secrets
    except StreamlitSecretNotFoundError:
        secrets = None

    if secrets is not None:
        for name in (
            "SPOTIFY_CLIENT_ID",
            "SPOTIFY_CLIENT_SECRET",
            "SPOTIFY_REDIRECT_URI",
            "SPOTIFY_REFRESH_TOKEN",
        ):
            try:
                value = secrets[name]
            except KeyError:
                continue
            if value:
                os.environ[name] = str(value).strip()

    from spotifyapi.spotifyclient import SpotifyClient

    return SpotifyFavoriteStatusAdapter(SpotifyClient())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AuthenticatedSpotifyFavoriteRecovery:
    """Lazily construct the authenticated provider for one confirmed observation."""

    def __init__(
        self,
        store: DataStore,
        account: str,
        producer: str,
        *,
        client_factory: Callable[[], FavoriteStatusProtocol] = _authenticated_spotify_client,
        observed_at: Callable[[], str] = _utc_now,
    ):
        self.store = store
        self.account = account
        self.producer = producer
        self.client_factory = client_factory
        self.observed_at = observed_at

    def recover(
        self, expected_request_id: str, *, remote_settled: bool = False,
    ) -> BatchPublication | None:
        request_id(expected_request_id)
        if remote_settled is not True:
            raise ValidationError(
                "Confirm that the previously displayed remote request has settled."
            )

        coordinator = FavoritesCoordinator(
            self.store, self.account, self.producer,
        )
        pending = coordinator.pending_change()
        if pending is None:
            return None
        if pending["request_id"] != expected_request_id:
            raise FavoriteOperationPending(
                "The pending favorite operation changed; review its current status before recovery."
            )

        service = CsvAcquisitionService(
            self.store,
            self.account,
            self.producer,
        )
        return service.recover_pending_favorite(
            self.observed_at,
            remote_settled=True,
            expected_request_id=expected_request_id,
            favorite_status_client=self.client_factory(),
        )

    def __call__(
        self, expected_request_id: str, *, remote_settled: bool,
    ) -> BatchPublication | None:
        return self.recover(
            expected_request_id, remote_settled=remote_settled,
        )
