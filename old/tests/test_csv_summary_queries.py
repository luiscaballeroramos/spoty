import copy
import unittest
from unittest.mock import patch

from spoty.domain.listening import ValidationError, canonical_json
from spoty.services.summary_queries import SummaryQueryService
from spoty.services.coverage import coverage_projection
from spoty.storage.contracts import StorageError
from spoty.storage.csv_codec import JSON_NULL, SCHEMAS
from spoty.storage.snapshots import Snapshot, dataset_partitions, descriptor


class MemoryStore:
    def __init__(self):
        self.commits = {}
        self.current = "a" * 40
        self.reads = []

    def head(self):
        return self.current

    def list_paths(self, commit, prefix=None):
        return sorted(path for path in self.commits[commit]
                      if prefix is None or path == prefix or path.startswith(prefix + "/"))

    def read(self, commit, path):
        self.reads.append(path)
        if path not in self.commits[commit]:
            raise StorageError("Missing test partition.")
        return self.commits[commit][path]

    def publish(self, expected, changes, message):
        raise AssertionError("Summary queries must never publish.")


class CsvSummaryQueriesTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.datasets = {dataset: [] for dataset in SCHEMAS}

    def snapshot(self, commit="a" * 40, account="test-account"):
        manifest = {
            "schema_version": 6, "account_key": account, "snapshot_id": "a" * 32,
            "parent_snapshot_id": None, "published_at": "2026-11-09T10:00:00Z", "datasets": {},
        }
        files = {}
        self.datasets["metadata_coverage"] = coverage_projection(self.datasets, include_orphans=True)
        for dataset, rows in self.datasets.items():
            parts = []
            for name, data in dataset_partitions(dataset, rows).items():
                relative = f"datasets/{dataset}/{name}"
                files[f"accounts/{account}/{relative}"] = data
                parts.append(descriptor(relative, data, SCHEMAS[dataset]))
            manifest["datasets"][dataset] = {
                "schema_version": SCHEMAS[dataset].version, "bucket_count": 16, "partitions": parts,
            }
        files[f"accounts/{account}/manifest.json"] = canonical_json(manifest).encode()
        self.store.commits[commit] = files
        self.store.current = commit
        return Snapshot.load(self.store, account)

    def seed(self):
        self.datasets["artists"] = [{"id": "artist-a", "name": "Artist A"}, {"id": "artist-b", "name": "Artist B"}]
        self.datasets["albums"] = [{
            "id": "album", "name": "Album", "artists": ["artist-a"], "tracks": None,
            "total_tracks": 2, "images": [{"url": "synthetic-image"}], "release_year": 2020,
        }]
        self.datasets["tracks"] = [{
            "id": key, "name": key.upper(), "duration_ms": 180000, "album_id": "album",
            "album_track": None, "disc_number": 1, "artists_ids": ["artist-a", "artist-b"],
            "explicit": 0, "popularity": 0,
        } for key in ("track-a", "track-b", "track-c")]
        self.datasets["liked_tracks"] = [{"id": "favorite-without-catalog"}]
        self.datasets["listening_events"] = [
            {"track_id": track_id, "played_at": when,
             "context_source": None, "context_type": None, "context_id": None}
            for track_id, when in (
                ("track-a", 1), ("track-a", 2), ("track-b", 3), ("track-b", 4),
                ("track-c", 4), ("uncataloged", 4),
            )
        ]

    def metadata(self, entity, entity_id, data):
        return {
            f"spotify_{entity}_id": entity_id, "normalized_data": data, "field_metadata": {},
            "raw_response": {"must": "not-be-read"}, "missing_requested": [],
            "captured_at": "2026-11-09T10:00:00Z", "last_attempt_status": "matched",
            "last_attempt_at": "2026-11-09T10:00:00Z", "last_success_at": "2026-11-09T10:00:00Z",
            "next_attempt_at": None, "last_error_code": None, "last_success_run_id": "run",
        }

    def test_empty_snapshot_contracts(self):
        service = SummaryQueryService(self.snapshot())
        self.assertEqual(service.dashboard_signature(), ("", 0, 0, 0, 0, 0, 0))
        self.assertEqual(service.recent_listening_events(), [])
        self.assertEqual(service.top_listened_genres(), {
            "total_listens": 0, "classified_listens": 0,
            "listened_tracks": 0, "classified_tracks": 0, "genres": [],
        })
        self.assertEqual(service.metadata_overview("tracks")["enriched"], 0)

    def test_counts_signature_and_tied_latest_track_match_sql_order(self):
        self.seed()
        service = SummaryQueryService(self.snapshot())
        self.assertEqual(service.library_counts(), {
            "total_tracks": 3, "total_artists": 2, "total_albums": 1,
            "total_liked_tracks": 1, "total_listening_events": 6,
        })
        self.assertEqual(service.dashboard_signature(), ("uncataloged", 4, 3, 2, 1, 1, 6))

    def test_history_ranks_whole_history_before_limiting_and_keeps_left_joins(self):
        self.seed()
        service = SummaryQueryService(self.snapshot())
        rows = service.recent_listening_events(3)
        self.assertEqual([row["track_id"] for row in rows], ["track-b", "track-c", "uncataloged"])
        self.assertEqual([row["track_top_percent"] for row in rows], [50, 75, 100])
        self.assertEqual(rows[0], {
            "played_at": 4, "track_id": "track-b", "track_play_count": 2, "track_top_percent": 50,
            "track_name": "TRACK-B", "duration_ms": 180000, "artists_ids": ["artist-a", "artist-b"],
            "album_name": "Album", "album_images": [{"url": "synthetic-image"}], "release_year": 2020,
        })
        self.assertIsNone(rows[-1]["track_name"])
        self.assertIsNone(rows[-1]["artists_ids"])
        self.assertIsNone(rows[-1]["album_images"])
        self.assertEqual(service.recent_listening_events(0), rows[:1])

    def test_ranking_uses_ceiling_and_track_id_as_tiebreaker(self):
        self.seed()
        self.datasets["listening_events"] = self.datasets["listening_events"][:-1]
        service = SummaryQueryService(self.snapshot())
        rows = {row["track_id"]: row for row in service.recent_listening_events()}
        self.assertEqual([rows[key]["track_top_percent"] for key in ("track-a", "track-b", "track-c")], [34, 67, 100])

    def test_artist_lookup_deduplicates_and_empty_lookup_reads_nothing(self):
        self.seed()
        service = SummaryQueryService(self.snapshot())
        self.store.reads.clear()
        self.assertEqual(service.artist_names([]), {})
        self.assertEqual(self.store.reads, [])
        self.assertEqual(service.artist_names(["artist-b", "artist-b", "missing"]), {"artist-b": "Artist B"})

    def test_labels_overlap_deduplicate_artists_and_track_labels(self):
        self.seed()
        self.datasets["tracks"][1]["artists_ids"] = []
        self.datasets["tracks"][2]["artists_ids"] = []
        self.datasets["smarterplaylists_artist_metadata"] = [
            self.metadata("artist", "artist-a", {"genres": [" Rock ", "rock", "Jazz", ""]}),
            self.metadata("artist", "artist-b", {"genres": ["rock", "soul"]}),
        ]
        service = SummaryQueryService(self.snapshot())
        result = service.top_listened_genres()
        self.assertEqual(result["classified_listens"], 2)
        self.assertEqual(result["classified_tracks"], 1)
        self.assertEqual(result["total_listens"], 6)
        self.assertEqual(result["listened_tracks"], 4)
        self.assertEqual(result["genres"], [{
            "genre": genre, "weighted_listens": 2.0, "share_percent": 100.0,
        } for genre in ("jazz", "rock", "soul")])
        self.assertEqual(len(service.top_listened_genres(limit=1)["genres"]), 1)
        self.assertEqual(sum(row["share_percent"] for row in result["genres"]), 300.0)

    def test_metadata_coverage_preserves_zero_false_and_ignores_negative_and_uncataloged(self):
        self.seed()
        self.datasets["smarterplaylists_track_metadata"] = [
            self.metadata("track", "track-a", {
                "energy": 0, "explicit": False, "empty": [], "blank": " ", "none": None,
                "object": {}, "nested": {"empty": None},
            }),
            self.metadata("track", "unknown", {"energy": 1}),
        ]
        self.datasets["lastfm_track_metadata"] = [
            {"spotify_track_id": "track-a", "match_status": "not_found",
             "normalized_data": None, "raw_response": {"error": 6}, "fetched_at": "date"},
            {"spotify_track_id": "track-b", "match_status": "matched",
             "normalized_data": {"title": "Title", "count": 0}, "raw_response": {}, "fetched_at": "date"},
        ]
        self.datasets["musicbrainz_recording_metadata"] = [{
            "spotify_track_id": "track-b", "match_status": "matched", "recording_mbid": "id",
            "normalized_data": {"title": "Title"}, "raw_response": {}, "fetched_at": "date",
        }]
        self.datasets["acousticbrainz_analysis_metadata"] = [{
            "spotify_track_id": "track-c", "match_status": "found", "recording_mbid": "id",
            "analysis_level": "low-level", "normalized_data": {"lowlevel": {"value": 0}},
            "raw_response": {}, "fetched_at": "date",
        }]
        result = SummaryQueryService(self.snapshot()).metadata_overview("tracks")
        self.assertEqual((result["total"], result["enriched"]), (3, 3))
        sources = {row["source"]: row for row in result["sources"]}
        self.assertEqual(sources["SmarterPlaylists"]["available"], 1)
        self.assertEqual(sources["SmarterPlaylists"]["fields"], [
            {"field": field, "available": 1} for field in ("energy", "explicit", "nested")
        ])
        self.assertEqual(sources["Last.fm"]["available"], 1)
        self.assertEqual(sources["AcousticBrainz (low-level)"]["available"], 1)
        self.assertEqual(sources["AcousticBrainz (high-level)"]["available"], 0)
        spotify_fields = {row["field"]: row["available"] for row in sources["Spotify"]["fields"]}
        self.assertEqual(spotify_fields["explicit"], 3)
        self.assertEqual(spotify_fields["popularity"], 3)
        self.assertNotIn("album_track", spotify_fields)

    def test_artist_album_coverage_does_not_read_track_providers(self):
        self.seed()
        for entity in ("artists", "albums"):
            service = SummaryQueryService(self.snapshot())
            self.store.reads.clear()
            result = service.metadata_overview(entity)
            self.assertEqual(len(result["sources"]), 2)
            self.assertFalse(any("lastfm" in path or "acousticbrainz" in path for path in self.store.reads))

    def test_json_nulls_preserve_sql_history_and_coverage_contracts(self):
        self.seed()
        self.datasets["albums"][0]["images"] = JSON_NULL
        self.datasets["tracks"][1]["artists_ids"] = JSON_NULL
        self.datasets["smarterplaylists_artist_metadata"] = [
            self.metadata("artist", "artist-a", JSON_NULL),
        ]
        service = SummaryQueryService(self.snapshot())
        self.assertIsNone(service.recent_listening_events()[0]["artists_ids"])
        self.assertIsNone(service.recent_listening_events()[0]["album_images"])
        self.assertEqual(service.top_listened_genres()["classified_tracks"], 0)
        coverage = service.metadata_overview("albums")
        self.assertNotIn("images", [row["field"] for row in coverage["sources"][0]["fields"]])

    def test_genre_limit_is_clamped_to_exact_twenty(self):
        self.seed()
        self.datasets["smarterplaylists_artist_metadata"] = [
            self.metadata("artist", "artist-a", {"genres": [f"genre-{index:02}" for index in range(25)]}),
        ]
        service = SummaryQueryService(self.snapshot())
        self.assertEqual(len(service.top_listened_genres(100)["genres"]), 20)
        self.assertEqual(len(service.top_listened_genres(0)["genres"]), 1)

    def test_pulse_uses_existing_projection_reader_and_no_raw_metadata(self):
        self.seed()
        self.datasets["track_audio"] = [{
            "spotify_track_id": "track-a", "name": "Projected", "audio": {"energy": 0.5},
        }]
        service = SummaryQueryService(self.snapshot())
        self.store.reads.clear()
        rows = service.recent_audio_history("track-a", now=4, hours=None, current_started_at=2)
        self.assertEqual(rows[-1], {
            "kind": "current", "track_id": "track-a", "played_at": None,
            "name": "TRACK-A", "audio": {"energy": 0.5},
        })
        self.assertEqual([row["played_at"] for row in rows if row["track_id"] == "track-a"], [1, None])
        self.assertFalse(any("_metadata/" in path for path in self.store.reads))

    def test_snapshot_is_pinned_and_pending_batches_do_not_change_counts(self):
        self.seed()
        old = self.snapshot()
        old_counts = SummaryQueryService(old).library_counts()
        self.datasets["listening_events"].append({
            **self.datasets["listening_events"][0], "played_at": 100,
        })
        new = self.snapshot("b" * 40)
        path = "accounts/test-account/inbox/listening_capture/2026-11/pending.csv"
        self.store.commits[new.commit_id][path] = b"not-yet-consolidated"
        self.assertEqual(SummaryQueryService(old).library_counts(), old_counts)
        self.assertEqual(SummaryQueryService(new).library_counts()["total_listening_events"], 7)
        self.assertEqual(new.pending_batches(), 1)
        self.assertEqual(old.pending_batches(), 0)

    def test_account_scope_and_mutated_results_do_not_leak(self):
        self.seed()
        snapshot = self.snapshot()
        self.datasets["tracks"] = []
        other_files = self.snapshot("b" * 40, "other-account")
        self.store.commits[other_files.commit_id].update(copy.deepcopy(self.store.commits[snapshot.commit_id]))
        self.assertEqual(SummaryQueryService(snapshot).library_counts()["total_tracks"], 3)
        self.assertEqual(SummaryQueryService(other_files).library_counts()["total_tracks"], 0)
        service = SummaryQueryService(Snapshot.load(self.store, "test-account"))
        row = service.recent_listening_events()[0]
        row["artists_ids"].append("mutated")
        self.assertNotIn("mutated", service.recent_listening_events()[0]["artists_ids"])

    def test_corrupted_partition_propagates_error(self):
        self.seed()
        snapshot = self.snapshot()
        path = next(path for path in self.store.commits[snapshot.commit_id] if "/datasets/tracks/" in path)
        self.store.commits[snapshot.commit_id][path] += b"corrupt"
        with self.assertRaises(ValidationError):
            SummaryQueryService(snapshot).library_counts()

    def test_invalid_queries_and_unmigrated_taxonomy_fail_before_reads(self):
        service = SummaryQueryService(self.snapshot())
        service.snapshot.manifest["datasets"] = {
            key: value for key, value in service.snapshot.manifest["datasets"].items()
            if not key.startswith("genre")
        }
        with patch.object(service.snapshot.store, "read", side_effect=AssertionError("Unexpected read")):
            with self.assertRaisesRegex(ValidationError, "no CSV taxonomy"):
                service.top_listened_genres(grouping="families")
            for value in (True, "5", 1.5, None):
                with self.subTest(value=value), self.assertRaises(ValidationError):
                    service.recent_listening_events(value)
                with self.assertRaises(ValidationError):
                    service.top_listened_genres(value)
            with self.assertRaises(ValidationError):
                service.top_listened_genres(grouping="bad")
            with self.assertRaises(ValidationError):
                service.metadata_overview("bad")
            with self.assertRaises(ValidationError):
                service.artist_names([{}])
