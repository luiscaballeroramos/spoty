"""Synthetic end-to-end demo; repositories are temporary and no API is called."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from spoty.domain.listening import ListeningBatch
from spoty.domain.metadata import MetadataBatch
from spoty.services.consolidation import consolidate
from spoty.services.pulse_queries import PulseQueryService
from spoty.services.favorites import FavoriteQueryService, FavoritesCoordinator
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot


def main() -> None:
    artist = {"id": "R" * 22, "name": "Demo artist"}
    item = {
        "played_at": "2026-11-06T10:00:00Z",
        "context": {"uri": "spotify:playlist:demo"},
        "track": {
            "id": "T" * 22, "name": "Demo track", "duration_ms": 180_000,
            "artists": [artist], "explicit": False,
            "album": {
                "id": "A" * 22, "name": "Demo album", "artists": [artist],
                "total_tracks": 1, "images": [],
            },
        },
    }
    with TemporaryDirectory(prefix="spoty-demo-") as temporary:
        store = LocalGitStore.initialize(Path(temporary) / "data.git")
        batch = ListeningBatch.from_items(
            "account-demo", "demo", "2026-11-06T10:05:00Z", [item, item],
        )
        saved = store.publish_batch(batch)
        values = {
            "id": "T" * 22, "uri": "spotify:track:" + "T" * 22, "name": "Demo track",
            "energy": 0, "tempo": 120, "explicit": False,
        }
        metadata = MetadataBatch.from_smarterplaylists(
            batch.account_key, "pulse-demo", "2026-11-06T10:05:00Z", [{
                "captured_at": "2026-11-06T10:00:00Z",
                "entities": {"track": {
                    "values": values, "conflicts": {}, "missing_requested": [],
                    "raw": {"status": "complete", "playlist_uri": None, "tracks": [values]},
                }},
            }], live_track=True,
        )
        metadata_saved = store.publish_batch(metadata)
        favorites = FavoritesCoordinator(store, batch.account_key, "favorites-demo")
        favorites.reserve_snapshot("1" * 32, "2026-11-06T10:00:00Z")
        favorites.begin_change("2" * 32, "T" * 22, True, "2026-11-06T10:01:00Z")
        favorite_pending = favorites.pending_change() is not None
        # Synthetic observation only; no remote action is executed.
        favorites.resolve_change(
            "2" * 32, True, "2026-11-06T10:02:00Z",
            resolution="confirmed", remote_settled=True,
        )
        stale_snapshot = favorites.submit_snapshot(
            "1" * 32, "2026-11-06T10:03:00Z",
            [{"offset": 0, "limit": 50, "total": 0, "next": None, "items": []}],
        )
        before = Snapshot.load(store, batch.account_key)
        result = consolidate(store, batch.account_key)
        after = Snapshot.load(store, batch.account_key)
        print(json.dumps({
            "publication": saved.status,
            "pending_before": before.pending_batches(),
            "pending_after": after.pending_batches(),
            "saved_events": result.saved_events,
            "duplicate_events": result.duplicate_events,
            "metadata_publication": metadata_saved.status,
            "saved_metadata": result.saved_metadata,
            "favorite_pending_before_confirmation": favorite_pending,
            "stale_favorite_snapshot": stale_snapshot.status,
            "saved_favorites": result.saved_favorites,
            "favorite_after": FavoriteQueryService(after).is_liked("T" * 22),
            "pulse_audio": PulseQueryService(after).recent_audio_history("T" * 22, 1)[-1]["audio"],
            "tracks": len(after.rows("tracks")),
            "historical_read_unchanged": before.rows("listening_events") == [],
            "retry": store.publish_batch(batch).status,
            "metadata_retry": store.publish_batch(metadata).status,
        }, indent=2))


if __name__ == "__main__":
    main()
