"""Explicit, additive taxonomy writes; provider metadata is never changed."""

from services.genre_taxonomy import normalize_label, validate_graph, validate_seed


class GenreTaxonomyRepository:
    def __init__(self, database_factory=None):
        self._database_factory = database_factory

    def _database(self, database_url):
        if self._database_factory is not None:
            return self._database_factory(database_url)
        from register.db import SimpleDB

        return SimpleDB(database_url)

    def import_seed(self, database_url, seed):
        validate_seed(seed)
        counts = {"genres_added": 0, "relations_added": 0, "mappings_added": 0}
        origin = f"seed:{seed['version']}"
        with self._database(database_url) as database:
            with database.transaction():
                cursor = database.cursor
                cursor.execute("SELECT pg_advisory_xact_lock(1936748392)")
                cursor.execute("SELECT id FROM genres")
                genre_ids = {row["id"] for row in cursor.fetchall()}
                genre_ids.update(genre["id"] for genre in seed["genres"])
                cursor.execute("SELECT parent_id, child_id FROM genre_relations")
                relations = {(row["parent_id"], row["child_id"]) for row in cursor.fetchall()}
                relations.update((parent, genre["id"]) for genre in seed["genres"] for parent in genre["parents"])
                validate_graph(genre_ids, relations)
                cursor.executemany("""INSERT INTO genres (id, name, introduced_by)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    [(genre["id"], genre["name"], origin) for genre in seed["genres"]])
                counts["genres_added"] = cursor.rowcount
                relation_values = [(parent, genre["id"], origin)
                                   for genre in seed["genres"] for parent in genre["parents"]]
                if relation_values:
                    cursor.executemany("""INSERT INTO genre_relations (parent_id, child_id, introduced_by)
                        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", relation_values)
                    counts["relations_added"] = cursor.rowcount
                mapping_values = [(normalize_label(label), genre["id"], origin)
                                  for genre in seed["genres"] for label in genre["labels"]]
                if mapping_values:
                    cursor.executemany("""INSERT INTO genre_label_mappings AS stored
                        (source, field, normalized_label, genre_id, status, introduced_by)
                        VALUES ('smarterplaylists', 'genres', %s, %s, 'approved', %s)
                        ON CONFLICT (source, field, normalized_label) DO UPDATE
                        SET genre_id = EXCLUDED.genre_id, status = 'approved',
                            introduced_by = EXCLUDED.introduced_by
                        WHERE stored.status = 'pending'""", mapping_values)
                    counts["mappings_added"] = cursor.rowcount
        return counts

    def discover_labels(self, database_url, apply=False):
        with self._database(database_url) as database:
            with database.transaction():
                cursor = database.cursor
                if apply:
                    cursor.execute("SELECT pg_advisory_xact_lock(1936748392)")
                cursor.execute("""SELECT DISTINCT lower(btrim(label.value)) AS label
                    FROM smarterplaylists_artist_metadata metadata
                    JOIN artists artist ON artist.id = metadata.spotify_artist_id
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        CASE WHEN jsonb_typeof(metadata.normalized_data->'genres') = 'array'
                        THEN metadata.normalized_data->'genres' ELSE '[]'::jsonb END
                    ) AS label(value)
                    WHERE btrim(label.value) <> '' AND NOT EXISTS (
                        SELECT 1 FROM genre_label_mappings mapping
                        WHERE mapping.source = 'smarterplaylists' AND mapping.field = 'genres'
                            AND mapping.normalized_label = lower(btrim(label.value))
                    ) ORDER BY label""")
                labels = [row["label"] for row in cursor.fetchall()]
                added = 0
                if apply:
                    for label in labels:
                        cursor.execute("""INSERT INTO genre_label_mappings
                            (source, field, normalized_label, status, introduced_by)
                            VALUES ('smarterplaylists', 'genres', %s, 'pending', 'discovery')
                            ON CONFLICT DO NOTHING""", (label,))
                        added += cursor.rowcount
        return {"unknown_labels": labels, "pending_added": added, "applied": apply}
