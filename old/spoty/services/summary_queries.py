from collections import Counter
from typing import Any

from spoty.domain.listening import ValidationError
from spoty.domain.taxonomy import TAXONOMY_DATASETS, family_labels
from spoty.services.pulse_queries import PulseQueryService
from spoty.services.coverage import ENTITIES, coverage_projection, metadata_overview, providers
from spoty.storage.csv_codec import JSON_NULL


def _limit(value: int, maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValidationError("Query limit must be an integer.")
    return max(1, min(maximum, value) if maximum is not None else value)


class SummaryQueryService(PulseQueryService):
    """Consolidated dashboard contracts pinned to one validated CSV snapshot."""

    def library_counts(self) -> dict[str, int]:
        return {
            f"total_{dataset}": len(self.snapshot.rows(dataset))
            for dataset in ("tracks", "artists", "albums", "liked_tracks", "listening_events")
        }

    def dashboard_signature(self) -> tuple[str, int, int, int, int, int, int]:
        counts = self.library_counts()
        latest = max(
            self.snapshot.rows("listening_events"),
            key=lambda row: (row["played_at"], row["track_id"]),
            default=None,
        )
        return (
            latest["track_id"] if latest else "", latest["played_at"] if latest else 0,
            counts["total_tracks"], counts["total_artists"], counts["total_albums"],
            counts["total_liked_tracks"], counts["total_listening_events"],
        )

    def recent_listening_events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = _limit(limit)
        events = self.snapshot.rows("listening_events")
        counts = Counter(event["track_id"] for event in events)
        ranked = sorted(counts, key=lambda track_id: (-counts[track_id], track_id))
        total = len(ranked)
        percentages = {
            track_id: (100 * rank + total - 1) // total
            for rank, track_id in enumerate(ranked, 1)
        }
        tracks = {row["id"]: row for row in self.snapshot.rows("tracks")}
        albums = {row["id"]: row for row in self.snapshot.rows("albums")}
        result = []
        # SQL only specifies descending time; track ID makes ties reproducible.
        for event in sorted(events, key=lambda row: (-row["played_at"], row["track_id"]))[:limit]:
            track = tracks.get(event["track_id"], {})
            album = albums.get(track.get("album_id"), {})
            result.append({
                "played_at": event["played_at"], "track_id": event["track_id"],
                "track_play_count": counts[event["track_id"]],
                "track_top_percent": percentages[event["track_id"]],
                "track_name": track.get("name"), "duration_ms": track.get("duration_ms"),
                "artists_ids": None if track.get("artists_ids") is JSON_NULL else track.get("artists_ids"),
                "album_name": album.get("name"),
                "album_images": None if album.get("images") is JSON_NULL else album.get("images"),
                "release_year": album.get("release_year"),
            })
        return result

    def artist_names(self, artist_ids: list[str]) -> dict[str, str]:
        if not isinstance(artist_ids, list) or any(not isinstance(value, str) for value in artist_ids):
            raise ValidationError("Artist lookup requires a list of identifiers.")
        if not artist_ids:
            return {}
        selected = set(artist_ids)
        return {
            row["id"]: row["name"] for row in self.snapshot.rows("artists")
            if row["id"] in selected
        }

    def top_listened_genres(self, limit: int = 10, grouping: str = "labels") -> dict[str, Any]:
        if grouping not in ("labels", "families"):
            raise ValidationError("Expected labels or families grouping.")
        limit = _limit(limit, 20)
        families = None
        if grouping == "families":
            if self.snapshot.manifest is None or not all(
                dataset in self.snapshot.manifest["datasets"] for dataset in TAXONOMY_DATASETS
            ):
                raise ValidationError("Snapshot has no CSV taxonomy; import a taxonomy seed first.")
            families = family_labels({
                dataset: self.snapshot.rows(dataset) for dataset in TAXONOMY_DATASETS
            })
        plays = Counter(row["track_id"] for row in self.snapshot.rows("listening_events"))
        tracks = {row["id"]: row for row in self.snapshot.rows("tracks")}
        artists = {
            row["spotify_artist_id"]: row["normalized_data"]
            for row in self.snapshot.rows("smarterplaylists_artist_metadata")
        }
        weighted: Counter[tuple[str, str]] = Counter()
        classified_tracks = classified_listens = 0
        for track_id, count in plays.items():
            track = tracks.get(track_id, {})
            artist_ids = track.get("artists_ids")
            if not isinstance(artist_ids, list):
                artist_ids = []
            genres = set()
            for artist_id in artist_ids:
                data = artists.get(artist_id, {})
                labels = data.get("genres") if isinstance(data, dict) else None
                if isinstance(labels, list):
                    genres.update(label.strip(" ").lower() for label in labels
                                  if isinstance(label, str) and label.strip(" "))
            resolved = (
                set().union(*(families.get(genre, set()) for genre in genres))
                if families is not None else {(genre, genre) for genre in genres}
            )
            if resolved:
                classified_tracks += 1
                classified_listens += count
                for genre in resolved:
                    weighted[genre] += count
        return {
            "total_listens": sum(plays.values()), "classified_listens": classified_listens,
            "listened_tracks": len(plays), "classified_tracks": classified_tracks,
            "genres": [{
                "genre": genre, "weighted_listens": float(count),
                "share_percent": 100 * count / classified_listens,
            } for (_, genre), count in sorted(
                weighted.items(), key=lambda item: (-item[1], item[0][1], item[0][0]),
            )[:limit]],
        }

    def metadata_overview(self, entity_type: str) -> dict[str, Any]:
        providers(entity_type)
        catalog = self.snapshot.rows(entity_type)
        if self.snapshot.manifest is not None and "metadata_coverage" in self.snapshot.manifest["datasets"]:
            projection = self.snapshot.rows("metadata_coverage")
        else:
            # Old snapshots remain readable; their next publication builds the projection.
            rows = {kind: [] for kind in ENTITIES}
            rows[entity_type] = catalog
            for kind, entity in ENTITIES.items():
                rows[f"smarterplaylists_{entity}_metadata"] = (
                    self.snapshot.rows(f"smarterplaylists_{entity}_metadata") if kind == entity_type else []
                )
            for dataset in ("lastfm_track_metadata", "musicbrainz_recording_metadata", "acousticbrainz_analysis_metadata"):
                rows[dataset] = self.snapshot.rows(dataset) if entity_type == "tracks" else []
            projection = coverage_projection(rows)
        return metadata_overview(
            entity_type, {row["id"] for row in catalog}, projection,
            include_orphans=self.snapshot.manifest is not None and self.snapshot.manifest["schema_version"] >= 6,
        )
