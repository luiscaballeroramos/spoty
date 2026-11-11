from collections.abc import Iterable
from typing import Any

from spoty.domain.listening import ValidationError
from spoty.storage.csv_codec import JSON_NULL


ENTITIES = {"tracks": "track", "artists": "artist", "albums": "album"}
PROVIDER_COVERAGE = {
    **{f"smarterplaylists_{entity}_metadata": {(kind, "SmarterPlaylists")}
       for kind, entity in ENTITIES.items()},
    "lastfm_track_metadata": {("tracks", "Last.fm")},
    "musicbrainz_recording_metadata": {("tracks", "MusicBrainz")},
    "acousticbrainz_analysis_metadata": {
        ("tracks", "AcousticBrainz (low-level)"), ("tracks", "AcousticBrainz (high-level)"),
    },
}


def providers(entity_type: str) -> list[str]:
    if entity_type not in ENTITIES:
        raise ValidationError("Expected tracks, artists or albums.")
    return ["Spotify", "SmarterPlaylists"] + ([
        "Last.fm", "MusicBrainz", "AcousticBrainz (low-level)", "AcousticBrainz (high-level)",
    ] if entity_type == "tracks" else [])


def valid_fields(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    return sorted(field for field, value in data.items() if not (
        value is None or value is JSON_NULL or value == [] or value == {}
        or (isinstance(value, str) and not value.strip(" "))
    ))


def coverage_projection(
    rows: dict[str, Iterable[dict[str, Any]]], *, include_orphans: bool = False,
) -> list[dict[str, Any]]:
    result = []
    catalog_ids = {}
    for kind in ENTITIES:
        catalog_ids[kind] = set()
        for row in rows[kind]:
            catalog_ids[kind].add(row["id"])
            fields = valid_fields({key: value for key, value in row.items() if key != "id"})
            if fields:
                result.append({"entity_type": kind, "entity_id": row["id"], "source": "Spotify", "fields": fields})
    for dataset in PROVIDER_COVERAGE:
        for row in provider_projection(dataset, rows[dataset]):
            if include_orphans or row["entity_id"] in catalog_ids[row["entity_type"]]:
                result.append(row)
    return result


def provider_projection(dataset: str, rows: Iterable[dict[str, Any]]):
    kind, source = sorted(PROVIDER_COVERAGE[dataset])[0]
    for row in rows:
        if dataset == "acousticbrainz_analysis_metadata":
            if row["match_status"] != "found" or row["analysis_level"] not in ("low-level", "high-level"):
                continue
            source = f"AcousticBrainz ({row['analysis_level']})"
        elif dataset in ("lastfm_track_metadata", "musicbrainz_recording_metadata") and row["match_status"] != "matched":
            continue
        fields = valid_fields(row["normalized_data"])
        if fields:
            yield {"entity_type": kind, "entity_id": row[f"spotify_{ENTITIES[kind]}_id"],
                   "source": source, "fields": fields}


def refresh_provider_projection(
    rows: dict[str, Iterable[dict[str, Any]]], projection: list[dict[str, Any]],
    updates: dict[str, Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    for kind in ENTITIES:
        metadata_overview(kind, {row["id"] for row in rows[kind]}, projection, include_orphans=True)
    affected = set().union(*(PROVIDER_COVERAGE[dataset] for dataset in updates))
    result = [row for row in projection if (row["entity_type"], row["source"]) not in affected]
    for entries in updates.values():
        result.extend(entries)
    return result


def metadata_overview(
    entity_type: str, catalog_ids: set[str], projection: list[dict[str, Any]],
    *, include_orphans: bool = False,
) -> dict[str, Any]:
    source_names = providers(entity_type)
    available = {name: set() for name in source_names}
    fields: dict[str, dict[str, set[str]]] = {name: {} for name in source_names}
    seen = set()
    for row in projection:
        kind, entity_id, source, names = (
            row["entity_type"], row["entity_id"], row["source"], row["fields"],
        )
        key = (kind, entity_id, source)
        if (kind not in ENTITIES or source not in providers(kind) or not isinstance(names, list)
                or not names or any(not isinstance(name, str) for name in names)
                or names != sorted(set(names)) or key in seen):
            raise ValidationError("Invalid metadata coverage projection.")
        seen.add(key)
        if kind != entity_type:
            continue
        if entity_id not in catalog_ids:
            if include_orphans and source != "Spotify":
                continue
            raise ValidationError("Coverage projection references an unknown catalog entity.")
        available[source].add(entity_id)
        for field in names:
            fields[source].setdefault(field, set()).add(entity_id)
    return {
        "total": len(catalog_ids),
        "enriched": len(set().union(*(available[name] for name in source_names if name != "Spotify"))),
        "sources": [{
            "source": source, "available": len(available[source]),
            "fields": [{"field": field, "available": len(ids)} for field, ids in
                       sorted(fields[source].items(), key=lambda item: (-len(item[1]), item[0]))],
        } for source in source_names],
    }


def extend_catalog_projection(
    catalog: dict[str, list[dict[str, Any]]], added: dict[str, list[dict[str, Any]]],
    projection: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for kind in ENTITIES:
        added_ids = {row["id"] for row in added[kind]}
        existing_ids = {row["id"] for row in catalog[kind]} - added_ids
        metadata_overview(kind, existing_ids, projection, include_orphans=True)
    result = list(projection)
    for kind, entries in added.items():
        for row in entries:
            fields = valid_fields({key: value for key, value in row.items() if key != "id"})
            if fields:
                result.append({"entity_type": kind, "entity_id": row["id"], "source": "Spotify", "fields": fields})
    return result
