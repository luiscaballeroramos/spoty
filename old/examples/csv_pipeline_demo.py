"""Entirely synthetic acquisition -> Git inbox -> consolidation -> queries/audit."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from spoty.cli.operations import local_operation
from spoty.services.acquisition import CsvAcquisitionService
from spoty.services.consolidation import consolidate
from spoty.services.favorites import FavoriteQueryService
from spoty.services.summary_queries import SummaryQueryService
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot


ACCOUNT = "synthetic-pipeline"
TRACK = "T" * 22
DATE = "2026-11-09T12:00:00Z"
LATER = "2026-11-09T12:05:00Z"


class SyntheticSpotify:
    def __init__(self):
        self.liked = True
        self.observations = 0

    def get_recently_played(self, limit=50):
        artist = {"id": "R" * 22, "name": "Synthetic artist"}
        return {"items": [{
            "played_at": DATE, "context": None, "original_synthetic_field": False,
            "track": {"id": TRACK, "name": "Synthetic track", "duration_ms": 180000,
                      "artists": [artist], "album": {"id": "A" * 22, "name": "Synthetic album",
                                                     "artists": [artist], "total_tracks": 1}},
        }]}

    def get_liked_songs(self, limit=50, offset=0):
        return {"items": [{"track": {"id": TRACK}}] if self.liked else [],
                "limit": limit, "offset": offset, "total": int(self.liked), "next": None}

    def are_tracks_liked(self, track_ids):
        self.observations += 1
        return {track_id: self.liked for track_id in track_ids}


def run_demo() -> dict:
    with TemporaryDirectory(prefix="spoty-pipeline-") as directory:
        store = LocalGitStore.initialize(Path(directory) / "data.git")
        spotify = SyntheticSpotify()
        acquisition = CsvAcquisitionService(store, ACCOUNT, "synthetic-pipeline", spotify=spotify)
        original = Snapshot.load(store, ACCOUNT)
        listening = acquisition.acquire_recent_listening(DATE)
        retry = acquisition.acquire_recent_listening(DATE)
        acquisition.acquire_favorites_snapshot("1" * 32, DATE, lambda: DATE)
        values = {"id": TRACK, "uri": f"spotify:track:{TRACK}", "name": "Synthetic track", "energy": 0.5}
        acquisition.publish_smarterplaylists([{
            "captured_at": DATE, "entities": {"track": {
                "values": values, "conflicts": {}, "missing_requested": [],
                "raw": {"status": "complete", "playlist_uri": None, "tracks": [values]},
            }},
        }], DATE, live_track=True)
        first_result = consolidate(store, ACCOUNT)
        first = Snapshot.load(store, ACCOUNT)
        first_counts = SummaryQueryService(first).library_counts()
        acquisition.publish_enrichment([{
            "provider": "lastfm", "spotify_track_id": TRACK, "match_status": "matched",
            "normalized_data": {"name": "Synthetic Last.fm", "zero": 0}, "raw_response": {"synthetic": True},
        }], LATER)
        metadata_result = consolidate(store, ACCOUNT)
        acquisition.favorites.begin_change("2" * 32, TRACK, False, LATER)
        # A settled synthetic observation, not an actual Spotify mutation.
        spotify.liked = False
        acquisition.recover_pending_favorite(lambda: LATER, remote_settled=True)
        favorite_result = consolidate(store, ACCOUNT)
        final = Snapshot.load(store, ACCOUNT)
        final_counts = SummaryQueryService(final).library_counts()
        audit = local_operation("audit", store.path, ACCOUNT)
        checks = (
            listening is not None and listening.status == "saved",
            retry is not None and retry.status == "already_saved",
            first_result.accepted_batches == 3 and first_result.rejected_batches == 0,
            first_counts["total_tracks"] == first_counts["total_listening_events"] == first_counts["total_liked_tracks"] == 1,
            metadata_result.saved_metadata == 1,
            favorite_result.removed_favorites == 1 and spotify.observations == 1,
            final_counts == {**first_counts, "total_liked_tracks": 0},
            FavoriteQueryService(final).liked_ids() == set(),
            SummaryQueryService(final).metadata_overview("tracks")["sources"][2]["available"] == 1,
            final.pending_batches() == 0,
            SummaryQueryService(first).library_counts() == first_counts,
            original.rows("listening_events") == [],
            audit["verified_captures"] == 5,
        )
        if not all(checks):
            raise AssertionError("Synthetic CSV acquisition pipeline verification failed.")
        return {
            "synthetic_only": True, "temporary_repository_removed_on_exit": True,
            "counts": final_counts, "verified_captures": audit["verified_captures"],
            "capture_retry_idempotent": True, "pending_batches": 0,
            "favorite_recovery_observation_only": True, "historical_snapshots_unchanged": True,
            "sql_connections": 0, "http_requests": 0,
        }


def main() -> None:
    print(json.dumps(run_demo(), indent=2))


if __name__ == "__main__":
    main()
