import copy
from typing import Any

from services.smarterplaylists import merge_metadata
from spoty.domain.listening import ValidationError, utc_timestamp
from spoty.domain.metadata import MetadataBatch, normalized_capture


PROVIDER_DATASETS = {
    "lastfm": "lastfm_track_metadata",
    "musicbrainz": "musicbrainz_recording_metadata",
    "acousticbrainz": "acousticbrainz_analysis_metadata",
}


def apply_metadata(
    batch: MetadataBatch, maps: dict[str, dict[tuple, dict[str, Any]]], processed_at: str,
) -> tuple[dict[str, dict[tuple, dict[str, Any]]], int, int]:
    """Stage the whole batch so a later conflict cannot leave partial metadata."""
    affected = {
        *PROVIDER_DATASETS.values(), "smarterplaylists_import_runs",
        *(f"smarterplaylists_{entity}_metadata" for entity in ("track", "artist", "album")),
    }
    staged = dict(maps)
    for dataset in affected:
        staged[dataset] = copy.deepcopy(maps[dataset])
    saved = duplicates = 0
    for payload in batch.payloads:
        provider = payload["provider"]
        if provider != "smarterplaylists":
            row = {key: value for key, value in payload.items() if key != "provider"}
            key = (row["spotify_track_id"],)
            if provider == "acousticbrainz":
                key += (row["analysis_level"],)
            target = staged[PROVIDER_DATASETS[provider]]
            if key in target:
                duplicates += 1
            else:
                target[key] = row
                saved += 1
            continue
        capture = normalized_capture(payload)
        if (capture.capture_id,) in staged["smarterplaylists_import_runs"]:
            duplicates += len(capture.records)
            continue
        for record in capture.records:
            core = {"track": "tracks", "artist": "artists", "album": "albums"}[record.entity]
            if (record.spotify_id,) not in staged[core] and not (
                payload["allow_unregistered_track"] and record.entity == "track"
            ):
                raise ValidationError("Metadata entity is missing from the catalog.")
            dataset = f"smarterplaylists_{record.entity}_metadata"
            existing = staged[dataset].get((record.spotify_id,), {})
            try:
                values, provenance = merge_metadata(existing, record, capture.capture_id)
            except ValueError as error:
                raise ValidationError("Conflicting metadata fields at the same time.") from error
            latest = not existing or capture.captured_at >= utc_timestamp(existing["captured_at"])
            staged[dataset][(record.spotify_id,)] = {
                f"spotify_{record.entity}_id": record.spotify_id,
                "normalized_data": values, "field_metadata": provenance,
                "raw_response": record.raw_response if latest else existing["raw_response"],
                "missing_requested": record.missing_requested if latest else existing["missing_requested"],
                "captured_at": capture.captured_at.isoformat().replace("+00:00", "Z")
                if latest else existing["captured_at"],
                "last_attempt_status": record.status,
                "last_attempt_at": processed_at, "last_success_at": processed_at,
                "next_attempt_at": None, "last_error_code": None,
                "last_success_run_id": capture.capture_id if latest else existing["last_success_run_id"],
            }
            saved += 1
        staged["smarterplaylists_import_runs"][(capture.capture_id,)] = {
            "capture_id": capture.capture_id,
            "captured_at": capture.captured_at.isoformat().replace("+00:00", "Z"),
            "imported_at": processed_at, "record_count": len(capture.records),
            "details": {
                "normalization_version": 1, "upstream_freshness": "unknown",
                "entities": [{"entity": record.entity, "id": record.spotify_id, "status": record.status}
                             for record in capture.records],
            },
        }
    return staged, saved, duplicates


def audio_projection(maps: dict[str, dict[tuple, dict[str, Any]]]) -> dict[tuple, dict[str, Any]]:
    fields = {
        "energy", "danceability", "valence", "acousticness", "instrumentalness",
        "tempo", "speechiness", "liveness", "loudness",
    }
    result = {}
    for key, row in maps["smarterplaylists_track_metadata"].items():
        data = row["normalized_data"]
        track = maps["tracks"].get(key, {})
        result[key] = {
            "spotify_track_id": key[0], "name": track.get("name") or data.get("name"),
            "audio": {field: data[field] for field in sorted(fields) if field in data},
        }
    return result
