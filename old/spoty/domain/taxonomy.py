from typing import Any

from services.genre_taxonomy import validate_graph
from spoty.domain.listening import ValidationError, utc_text


TAXONOMY_DATASETS = ("genres", "genre_relations", "genre_label_mappings")


def validate_taxonomy(datasets: dict[str, list[dict[str, Any]]]) -> None:
    genres = datasets["genres"]
    ids = {row["id"] for row in genres}
    if len(ids) != len(genres):
        raise ValidationError("Duplicate taxonomy genre.")
    for row in genres:
        if not row["id"] or not row["name"].strip(" ") or not row["introduced_by"]:
            raise ValidationError("Invalid taxonomy genre.")
        if utc_text(row["created_at"]) != row["created_at"]:
            raise ValidationError("Invalid taxonomy creation timestamp.")
    edges = [(row["parent_id"], row["child_id"]) for row in datasets["genre_relations"]]
    if len(set(edges)) != len(edges):
        raise ValidationError("Duplicate taxonomy relation.")
    try:
        validate_graph(ids, edges)
    except ValueError as error:
        raise ValidationError(str(error)) from error
    seen = set()
    for row in datasets["genre_label_mappings"]:
        label = row["normalized_label"]
        key = (row["source"], row["field"], label)
        if key in seen or not label or label != label.strip(" ").lower():
            raise ValidationError("Invalid or duplicate taxonomy label.")
        seen.add(key)
        if row["status"] == "approved":
            if row["genre_id"] not in ids:
                raise ValidationError("Approved taxonomy label requires an existing genre.")
        elif row["status"] not in ("pending", "excluded") or row["genre_id"] is not None:
            raise ValidationError("Invalid taxonomy mapping status or target.")
        if not row["introduced_by"] or utc_text(row["created_at"]) != row["created_at"]:
            raise ValidationError("Invalid taxonomy mapping provenance.")
    if any(not row["introduced_by"] for row in datasets["genre_relations"]):
        raise ValidationError("Invalid taxonomy relation provenance.")


def family_labels(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, set[tuple[str, str]]]:
    validate_taxonomy(datasets)
    names = {row["id"]: row["name"] for row in datasets["genres"]}
    parents: dict[str, set[str]] = {genre_id: set() for genre_id in names}
    for row in datasets["genre_relations"]:
        parents[row["child_id"]].add(row["parent_id"])
    roots: dict[str, set[tuple[str, str]]] = {}

    def ancestors(genre_id: str) -> set[tuple[str, str]]:
        if genre_id not in roots:
            roots[genre_id] = (
                set().union(*(ancestors(parent) for parent in parents[genre_id]))
                if parents[genre_id] else {(genre_id, names[genre_id])}
            )
        return roots[genre_id]

    return {
        row["normalized_label"]: ancestors(row["genre_id"])
        for row in datasets["genre_label_mappings"]
        if row["source"] == "smarterplaylists" and row["field"] == "genres"
        and row["status"] == "approved"
    }
