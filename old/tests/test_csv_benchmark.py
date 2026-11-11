import os
import shutil
import unittest

from examples.benchmark_csv import Profile, incremental_batch, metadata_batch, run_benchmark, synthetic_rows
from spoty.domain.listening import ValidationError
from spoty.domain.taxonomy import validate_taxonomy
from spoty.storage.csv_codec import SCHEMAS, decode_batch, decode_csv, encode_batch, encode_csv


class CsvBenchmarkTest(unittest.TestCase):
    def test_synthetic_profile_matches_exact_shapes_and_round_trips(self):
        profile = Profile(tracks=13, events=27, likes=3, raw_bytes_per_track=129, incremental_events=2)
        rows = synthetic_rows(profile)
        self.assertEqual(len(rows["tracks"]), 13)
        self.assertEqual(len(rows["listening_events"]), 27)
        self.assertEqual(len(rows["liked_tracks"]), 3)
        self.assertEqual(len(rows["albums"]), 2)
        self.assertEqual(len(rows["artists"]), 5)
        self.assertEqual(len(rows["track_audio"]), 13)
        self.assertEqual(len({(row["track_id"], row["played_at"]) for row in rows["listening_events"]}), 27)
        raw = [row["raw_response"]["synthetic"] for row in rows["smarterplaylists_track_metadata"]]
        self.assertTrue(all(len(value.encode()) == 129 for value in raw))
        self.assertEqual(len(set(raw)), 13)
        validate_taxonomy(rows)
        for dataset, entries in rows.items():
            with self.subTest(dataset=dataset):
                self.assertEqual(decode_csv(SCHEMAS[dataset], encode_csv(SCHEMAS[dataset], entries)), entries)
        self.assertEqual(rows, synthetic_rows(profile))
        self.assertEqual(len(incremental_batch(profile).payloads), 2)
        new_ids = {row["track_id"] for row in incremental_batch(profile, new_tracks=True).payloads}
        self.assertEqual(len(new_ids), 2)
        self.assertFalse(new_ids & {row["id"] for row in rows["tracks"]})
        for smarterplaylists in (False, True):
            update = metadata_batch(profile, smarterplaylists=smarterplaylists)
            self.assertEqual(len(update.payloads), 2)
            self.assertEqual(decode_batch(encode_batch(update)), update)

    def test_invalid_profiles_are_rejected(self):
        for values in (
            {"tracks": 0}, {"tracks": True}, {"events": -1}, {"likes": 10000},
            {"raw_bytes_per_track": -1}, {"raw_bytes_per_track": 20 * 1024 * 1024},
            {"incremental_events": 0},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                synthetic_rows(Profile(**values))

    @unittest.skipUnless(os.name == "nt" and shutil.which("git"), "Windows memory API and Git required")
    def test_benchmark_measures_real_local_io_and_verifies_increment(self):
        report = run_benchmark(Profile(tracks=4, events=7, likes=2, raw_bytes_per_track=128, incremental_events=2))
        self.assertEqual(report["verified"]["final_events"], 9)
        self.assertTrue(report["verified"]["historical_snapshot_unchanged"])
        self.assertEqual(report["verified"]["new_tracks"]["final_counts"]["total_tracks"], 6)
        self.assertEqual(report["verified"]["new_tracks"]["final_counts"]["total_listening_events"], 11)
        self.assertEqual(report["verified"]["new_tracks"]["enriched_tracks"], 4)
        self.assertEqual(report["verified"]["new_tracks"]["classified_listens"], 11)
        for provider in ("lastfm", "smarterplaylists"):
            self.assertEqual(report["verified"][provider]["saved_metadata"], 2)
            self.assertTrue(report["verified"][provider]["coverage_verified"])
            self.assertTrue(report["verified"][provider]["previous_snapshot_unchanged"])
            self.assertGreater(report["phases"][f"consolidate_{provider}"]["read_bytes"], 0)
        self.assertGreater(report["initial_partition_bytes"], 0)
        self.assertGreater(report["repository_growth_bytes"], 0)
        self.assertGreater(report["phases"]["counts"]["git_calls"], 0)
        self.assertEqual(report["phases"]["counts"]["git_calls"], 5)
        self.assertEqual(report["phases"]["metadata_tracks"]["git_calls"], 2)
        self.assertGreater(report["phases"]["metadata_tracks"]["read_bytes"], 0)
        for values in report["phases"].values():
            self.assertGreaterEqual(values["seconds"], 0)
            self.assertGreater(values["sampled_rss_peak_bytes"], 0)
            self.assertGreaterEqual(values["process_lifetime_rss_peak_bytes"], values["rss_after_bytes"])
