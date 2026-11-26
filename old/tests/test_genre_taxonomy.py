import copy
import json
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from register.genre_taxonomy import GenreTaxonomyRepository
from register.migrations import apply_migrations
from services.genre_taxonomy import load_seed, normalize_label, render_taxonomy_html, render_taxonomy_markdown, validate_graph, validate_seed
from services.summary_queries import SummaryQueryService


class GenreTaxonomyTest(unittest.TestCase):
    def test_seed_accepts_multiple_parents_and_normalizes_labels(self):
        """Load the packaged seed and retain cross-family relationships."""
        seed = load_seed()
        folk_rock = next(genre for genre in seed["genres"] if genre["id"] == "folk-rock")
        self.assertEqual(folk_rock["parents"], ["folk", "rock"])
        self.assertEqual(normalize_label("  Indie Rock  "), "indie rock")

    def test_seed_rejects_cycles_unknown_parents_and_duplicate_labels(self):
        """Reject ambiguous labels and invalid graphs before any database writes."""
        for defect in ("cycle", "unknown", "duplicate"):
            with self.subTest(defect=defect):
                seed = copy.deepcopy(load_seed())
                if defect == "cycle":
                    seed["genres"][0]["parents"] = ["grunge"]
                elif defect == "unknown":
                    seed["genres"][0]["parents"] = ["missing"]
                else:
                    seed["genres"][1]["labels"].append(" ROCK ")
                with self.assertRaises(ValueError):
                    validate_seed(seed)

    def test_combined_graph_rejects_cycles_across_imports(self):
        """Validate the union of stored and imported relationships."""
        with self.assertRaises(ValueError):
            validate_graph({"rock", "grunge"}, [("rock", "grunge"), ("grunge", "rock")])

    def test_reviewed_labels_preserve_distinct_styles_and_cross_family_links(self):
        """Keep key styles distinct and contextual tags outside forced musical genres."""
        seed = load_seed()
        genres = {genre["id"]: genre for genre in seed["genres"]}
        mappings = {label: genre["id"] for genre in seed["genres"] for label in genre["labels"]}
        self.assertEqual(mappings["argentine trap"], "latin-trap")
        self.assertEqual(set(genres["latin-trap"]["parents"]), {"urbano-latino", "trap"})
        self.assertEqual(set(genres["jazz-rap"]["parents"]), {"jazz", "hip-hop"})
        self.assertNotEqual(mappings["afrobeat"], mappings["afrobeats"])
        self.assertEqual(mappings["brazilian funk"], "funk-carioca")
        self.assertNotIn("funk", genres["funk-carioca"]["parents"])
        self.assertEqual(genres[mappings["indie"]]["parents"], ["contextual"])
        self.assertEqual(genres[mappings["christmas"]]["parents"], ["contextual"])
        self.assertNotIn("latin", genres["tribal-house"]["parents"])

    def test_diagram_renders_every_relationship_and_original_label(self):
        """Generate all graph edges and exact labels, including cross-family parents."""
        seed = load_seed()
        markdown = render_taxonomy_markdown(seed)
        self.assertIn("g_hip_hop -.-> g_jazz_rap", markdown)
        self.assertIn("g_jazz --> g_jazz_rap", markdown)
        for genre in seed["genres"]:
            genre_node = "g_" + genre["id"].replace("-", "_")
            self.assertIn(genre_node, markdown)
            for parent in genre["parents"]:
                parent_node = "g_" + parent.replace("-", "_")
                self.assertIn(f"{parent_node} --> {genre_node}", markdown)
            for label in genre["labels"]:
                self.assertIn(f"`{label}`", markdown)

    def test_committed_diagram_matches_seed(self):
        """Keep the checked-in graphical schema synchronized with the seed."""
        diagram = Path(__file__).resolve().parents[1] / "GENRE_TAXONOMY.md"
        self.assertEqual(diagram.read_text(encoding="utf-8"), render_taxonomy_markdown(load_seed()))

    def test_html_viewer_embeds_all_data_without_network_dependencies(self):
        """Build a self-contained explorer and protect the embedded JSON boundary."""
        seed = load_seed()
        seed["genres"][0]["name"] = '</script><script>alert("unsafe")</script>'
        html = render_taxonomy_html(seed)
        payload = html.split('<script type="application/json" id="taxonomy-data">', 1)[1].split('</script>', 1)[0]
        self.assertEqual(json.loads(payload), seed)
        self.assertNotIn('</script>', payload)
        self.assertNotIn('__TAXONOMY_DATA__', html)
        self.assertNotIn('<script src=', html)
        self.assertIn('id="family"', html)
        self.assertIn('id="search"', html)

    def test_committed_viewer_matches_seed_and_template(self):
        """Keep the standalone HTML synchronized with its source data and template."""
        viewer = Path(__file__).resolve().parents[1] / "GENRE_TAXONOMY.html"
        self.assertEqual(viewer.read_text(encoding="utf-8"), render_taxonomy_html(load_seed()))

    def _repository(self):
        database = MagicMock()
        database.__enter__.return_value = database
        database.cursor.fetchall.return_value = []
        database.cursor.rowcount = 1
        database.cursor.executemany.side_effect = lambda _query, values: setattr(database.cursor, "rowcount", len(values))
        return GenreTaxonomyRepository(lambda _url: database), database

    def test_import_is_additive_and_rejects_combined_cycles_before_writing(self):
        """Keep existing rows untouched and reject cross-import cycles atomically."""
        repository, database = self._repository()
        result = repository.import_seed("unused", load_seed())
        self.assertEqual(result["genres_added"], len(load_seed()["genres"]))
        writes = [call.args[0] for call in database.cursor.executemany.call_args_list]
        self.assertEqual(len(writes), 3)
        self.assertTrue(all("ON CONFLICT DO NOTHING" in query or "WHERE stored.status = 'pending'" in query for query in writes))
        repository, database = self._repository()
        database.cursor.fetchall.side_effect = [[{"id": "rock"}, {"id": "grunge"}],
                                                [{"parent_id": "grunge", "child_id": "rock"}]]
        with self.assertRaises(ValueError):
            repository.import_seed("unused", load_seed())
        self.assertFalse(any("INSERT" in call.args[0] for call in database.cursor.execute.call_args_list))
        database.cursor.executemany.assert_not_called()

    def test_discovery_requires_apply_and_only_inserts_pending_labels(self):
        """Preview unknown labels without writes and never auto-classify them."""
        for apply in (False, True):
            with self.subTest(apply=apply):
                repository, database = self._repository()
                database.cursor.fetchall.return_value = [{"label": "new style"}]
                result = repository.discover_labels("unused", apply=apply)
                self.assertEqual(result["unknown_labels"], ["new style"])
                self.assertEqual(result["pending_added"], int(apply))
                writes = [call.args[0] for call in database.cursor.execute.call_args_list if "INSERT" in call.args[0]]
                self.assertEqual(len(writes), int(apply))
                if apply:
                    self.assertIn("'pending'", writes[0])


@unittest.skipUnless(os.getenv("SPOTY_TEST_DATABASE_URL"), "Explicit isolated PostgreSQL test URL required")
class PostgreSQLGenreTaxonomyTest(unittest.TestCase):
    def test_taxonomy_import_discovery_and_family_counts(self):
        """Exercise real SQL in a rolled-back schema, including overlapping families."""
        from config import require_database_url

        database_url = require_database_url(os.environ["SPOTY_TEST_DATABASE_URL"])
        schema = "spoty_genres_" + uuid4().hex
        with psycopg.connect(database_url, autocommit=True) as connection:
            with connection.transaction():
                connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
                self.assertIn("0007_genre_taxonomy", apply_migrations(connection))
                self.assertEqual(apply_migrations(connection), [])

                class Database:
                    def __enter__(self):
                        self.cursor = connection.cursor(row_factory=dict_row)
                        return self

                    def __exit__(self, *_args):
                        self.cursor.close()

                    def transaction(self):
                        return connection.transaction()

                class QueryConnection:
                    def cursor(self):
                        return connection.cursor(row_factory=dict_row)

                repository = GenreTaxonomyRepository(lambda _url: Database())
                queries = SummaryQueryService(database_url, lambda *_args, **_kwargs: nullcontext(QueryConnection()))
                seed = load_seed()
                self.assertEqual(repository.import_seed(database_url, seed)["genres_added"], len(seed["genres"]))
                self.assertTrue(all(value == 0 for value in repository.import_seed(database_url, seed).values()))
                connection.execute("UPDATE genres SET name = 'My Rock' WHERE id = 'rock'")
                connection.execute("""UPDATE genre_label_mappings SET genre_id = 'pop', introduced_by = 'manual'
                    WHERE normalized_label = 'rock'""")
                repository.import_seed(database_url, seed)
                self.assertEqual(connection.execute("SELECT name FROM genres WHERE id = 'rock'").fetchone()[0], "My Rock")
                self.assertEqual(connection.execute("SELECT genre_id FROM genre_label_mappings WHERE normalized_label = 'rock'").fetchone()[0], "pop")
                connection.execute("UPDATE genre_label_mappings SET genre_id = 'rock' WHERE normalized_label = 'rock'")
                connection.execute("UPDATE genres SET name = 'Rock' WHERE id = 'rock'")

                for artist_id, labels in (("artist-a", ["indie rock", "grunge", "folk rock", "new style"]),
                                          ("artist-b", ["grunge", "rock"]),
                                          ("artist-c", ["new style"]), ("artist-d", ["noise-label"])):
                    connection.execute("INSERT INTO artists (id, name) VALUES (%s, %s)", (artist_id, artist_id))
                    connection.execute("""INSERT INTO smarterplaylists_artist_metadata
                        (spotify_artist_id, normalized_data, field_metadata, missing_requested,
                         captured_at, last_attempt_status, last_success_run_id)
                        VALUES (%s, %s, '{}', '[]', NOW(), 'matched', 'test')""",
                        (artist_id, json.dumps({"genres": labels})))
                for track_id, artist_ids, plays in (("track-a", ["artist-a", "artist-b"], 3),
                                                    ("track-b", ["artist-c"], 1),
                                                    ("track-c", [], 1), ("track-d", ["artist-d"], 1)):
                    connection.execute("""INSERT INTO tracks (id, name, duration_ms, album_id, artists_ids)
                        VALUES (%s, %s, 1000, 'album', %s)""", (track_id, track_id, json.dumps(artist_ids)))
                    for played_at in range(plays):
                        connection.execute("INSERT INTO listening_events (track_id, played_at) VALUES (%s, %s)",
                                           (track_id, played_at))
                connection.execute("""INSERT INTO genre_label_mappings
                    (source, field, normalized_label, status, introduced_by)
                    VALUES ('smarterplaylists', 'genres', 'noise-label', 'excluded', 'manual')""")
                self.assertEqual(repository.discover_labels(database_url)["unknown_labels"], ["new style"])
                self.assertEqual(repository.discover_labels(database_url, apply=True)["pending_added"], 1)
                self.assertEqual(repository.discover_labels(database_url, apply=True)["pending_added"], 0)
                families = queries.top_listened_genres(grouping="families")
                self.assertEqual(families["total_listens"], 6)
                self.assertEqual(families["classified_listens"], 3)
                self.assertEqual({genre["genre"]: genre["weighted_listens"] for genre in families["genres"]},
                                 {"Rock": 3, "Folk": 3})
                self.assertEqual(queries.top_listened_genres()["classified_listens"], 5)

                expanded = copy.deepcopy(seed)
                expanded["genres"].append({"id": "new-style", "name": "New Style", "parents": ["pop"],
                                           "labels": ["new style", "noise-label"]})
                repository.import_seed(database_url, expanded)
                self.assertEqual(connection.execute("SELECT status FROM genre_label_mappings WHERE normalized_label = 'new style'").fetchone()[0], "approved")
                self.assertEqual(connection.execute("SELECT status FROM genre_label_mappings WHERE normalized_label = 'noise-label'").fetchone()[0], "excluded")
                self.assertEqual(queries.top_listened_genres(grouping="families")["classified_listens"], 4)
                cyclic = copy.deepcopy(seed)
                cyclic["genres"][0]["parents"] = ["new-style"]
                connection.execute("INSERT INTO genre_relations VALUES ('rock', 'new-style', 'manual')")
                with self.assertRaises(ValueError):
                    repository.import_seed(database_url, cyclic)
                raise psycopg.Rollback()
            self.assertIsNone(connection.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0])
