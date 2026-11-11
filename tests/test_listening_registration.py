import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import config
import publish_listening_events as publisher
import publish_data as data_publisher
from register.spotify_data import catalog_from_items
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.timestamps import parse_spotify_timestamp
from register.listeningevent import ListeningEvent


ITEM = {
    "track": {
        "id": "T" * 22, "name": 'Song, "title"', "duration_ms": 180000,
        "artists": [{"id": "R" * 22, "name": "Artist"}],
        "album": {
            "id": "A" * 22, "name": "Album", "total_tracks": 10,
            "artists": [{"id": "R" * 22, "name": "Artist"}],
            "release_date": "2020-02", "release_date_precision": "month",
        },
    },
    "played_at": "2026-11-06T10:00:00Z",
    "context": None,
}
CREDENTIALS = {
    "SPOTIFY_CLIENT_ID": "test-client",
    "SPOTIFY_CLIENT_SECRET": "test-secret",
    "SPOTIFY_REFRESH_TOKEN": "test-refresh",
}


class ListeningRegistrationTest(unittest.TestCase):
    def test_recent_plays_refreshes_token_and_returns_response(self):
        with (
            patch.dict(os.environ, CREDENTIALS, clear=True),
            patch("spotifyapi.spotifyclient.requests.post") as post,
            patch("spotifyapi.spotifyclient.requests.get") as get,
        ):
            post.return_value = Mock(
                status_code=200, json=lambda: {"access_token": "test-access"}
            )
            get.return_value = Mock(status_code=200, json=lambda: {"items": [ITEM]})
            self.assertEqual(SpotifyClient().get_recently_played(), {"items": [ITEM]})
            self.assertEqual(get.call_args.kwargs["params"], {"limit": 50})
            self.assertEqual(
                get.call_args.kwargs["headers"]["Authorization"], "Bearer test-access"
            )

    def test_missing_credentials_and_http_errors_are_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing SPOTIFY_CLIENT_ID"):
                SpotifyClient().get_recently_played()
        with (
            patch.dict(os.environ, CREDENTIALS, clear=True),
            patch("spotifyapi.spotifyclient.requests.post", return_value=Mock(status_code=401)),
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                SpotifyClient().get_recently_played()

    def test_flat_rows_preserve_utc_time_and_context(self):
        row = publisher.build_event_rows([ITEM])[0]
        self.assertEqual(row["played_at"], 1793959200)
        self.assertIsNone(row["context_id"])
        item = {**ITEM, "context": {"uri": "spotify:playlist:example"}}
        self.assertEqual(publisher.build_event_rows([item])[0]["context_id"], "example")
        self.assertEqual(
            parse_spotify_timestamp("2026-11-06T11:00:00+01:00").timestamp(),
            row["played_at"],
        )
        with self.assertRaises(ValueError):
            parse_spotify_timestamp("2026-11-06T10:00:00")

    def test_main_dispatches_with_root_client(self):
        with (
            patch.dict(os.environ, {"SPOTY_DATA_TOKEN": "test"}, clear=True),
            patch.object(publisher, "load_env_file"),
            patch.object(publisher, "SpotifyClient") as client,
            patch.object(publisher, "publish_data") as publish,
            patch("builtins.print"),
        ):
            client.return_value.get_recently_played.return_value = {"items": [ITEM]}
            publisher.main()
            publish.assert_called_once_with(
                {"events": publisher.build_event_rows([ITEM]), **catalog_from_items([ITEM])},
                "test", source="spotify-listening",
            )

    def test_recovered_modules_are_outside_old(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(config.__file__).parent, root)
        self.assertEqual(Path(publisher.__file__).parent, root)
        self.assertEqual(
            (root / "spotifyapi" / "timestamps.py").read_text(encoding="utf-8"),
            (root / "old" / "spotifyapi" / "timestamps.py").read_text(encoding="utf-8"),
        )

    def test_event_model_validates_keys_and_timezone(self):
        with self.assertRaises(ValueError):
            ListeningEvent("invalid", datetime.now(timezone.utc))
        with self.assertRaises(ValueError):
            ListeningEvent("T" * 22, datetime(2026, 11, 6))
        with self.assertRaises(ValueError):
            ListeningEvent("T" * 22, datetime(1969, 1, 1, tzinfo=timezone.utc))

    def test_dispatch_success_failure_and_empty_batch(self):
        rows = publisher.build_event_rows([ITEM])
        with patch.object(data_publisher.requests, "post") as post:
            post.return_value = Mock(status_code=204)
            data_publisher.publish_data({"events": rows}, "test-token", "test-source")
            self.assertEqual(post.call_args.kwargs["json"], {
                "event_type": "record-data",
                "client_payload": {"source": "test-source", "events": rows},
            })
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            post.return_value = Mock(status_code=403)
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                data_publisher.publish_data({"events": rows}, "test-token", "test-source")
            post.reset_mock()
            data_publisher.publish_data({"events": []}, "test-token", "test-source")
            post.assert_not_called()

    def test_invalid_items_fail_before_dispatch(self):
        for items in (None, [None], [{"track": None}], [{**ITEM, "context": "invalid"}]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                publisher.build_event_rows(items)

    def test_invalid_spotify_responses_are_explicit(self):
        with (
            patch.dict(os.environ, CREDENTIALS, clear=True),
            patch("spotifyapi.spotifyclient.requests.post") as post,
            patch("spotifyapi.spotifyclient.requests.get") as get,
        ):
            post.return_value = Mock(status_code=200, json=lambda: {})
            with self.assertRaisesRegex(ValueError, "access token"):
                SpotifyClient().get_recently_played()
            post.return_value = Mock(status_code=200, json=lambda: {"access_token": "test"})
            get.return_value = Mock(status_code=429)
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                SpotifyClient().get_recently_played()
            get.return_value = Mock(status_code=200, json=lambda: {"items": None})
            with self.assertRaisesRegex(ValueError, "recently-played"):
                SpotifyClient().get_recently_played()

    def test_catalog_extracts_original_headers_and_deduplicates(self):
        catalog = catalog_from_items([ITEM, ITEM])
        self.assertEqual(len(catalog["tracks"]), 1)
        self.assertEqual(len(catalog["artists"]), 1)
        self.assertEqual(catalog["albums"][0]["tracks"], ["T" * 22])
        self.assertEqual(catalog["albums"][0]["release_year"], 2020)
        self.assertIsNone(catalog["tracks"][0]["popularity"])

    def test_large_dispatches_are_split_without_losing_records(self):
        import json
        records = [{"id": str(index), "name": "á" * 2000} for index in range(20)]
        batches = data_publisher.build_dispatches({"artists": records}, "import")
        self.assertGreater(len(batches), 1)
        self.assertEqual(
            [row for batch in batches for row in batch["client_payload"].get("artists", [])],
            records,
        )
        self.assertTrue(all(
            len(json.dumps(batch).encode("utf-8")) <= data_publisher.MAX_DISPATCH_BYTES
            for batch in batches
        ))
        with patch.object(data_publisher.requests, "post") as post:
            with self.assertRaises(ValueError):
                data_publisher.publish_data(
                    {"artists": [{"name": "x" * 70000}]}, "test", "import"
                )
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
