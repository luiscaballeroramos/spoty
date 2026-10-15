import unittest
from unittest.mock import Mock, patch


class LastFmAdapterTest(unittest.TestCase):
    def test_import_does_not_make_network_requests(self):
        """Importing the adapter must not contact Last.fm."""
        with patch("requests.get", side_effect=AssertionError("unexpected request")):
            from metadata_sources.lastfm import LastFmAdapter

            LastFmAdapter("test-key")

    def test_get_track_uses_mbid_and_returns_validated_payload(self):
        """Fetch a valid track using its MBID without requesting Spotify data."""
        from metadata_sources.lastfm import LastFmAdapter

        payload = {"track": {"name": "Track", "mbid": "recording-id"}}
        response = Mock()
        response.json.return_value = payload
        request_get = Mock(return_value=response)

        result = LastFmAdapter("test-key", request_get=request_get).get_track(
            mbid="recording-id"
        )

        self.assertEqual(result, payload)
        response.raise_for_status.assert_called_once_with()
        request_get.assert_called_once_with(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "track.getInfo",
                "api_key": "test-key",
                "autocorrect": 1,
                "format": "json",
                "mbid": "recording-id",
            },
            timeout=10,
        )

    def test_get_track_rejects_missing_identity_and_malformed_response(self):
        """Reject incomplete lookup keys and invalid provider responses."""
        from metadata_sources.lastfm import LastFmAdapter

        adapter = LastFmAdapter("test-key", request_get=Mock())
        with self.assertRaises(ValueError):
            adapter.get_track(artist="Artist")

        response = Mock()
        response.json.return_value = {"track": []}
        adapter = LastFmAdapter("test-key", request_get=Mock(return_value=response))
        with self.assertRaises(ValueError):
            adapter.get_track(artist="Artist", track="Track")

    def test_get_track_preserves_api_error_code(self):
        """Expose provider API errors separately from malformed responses."""
        from metadata_sources.lastfm import LastFmAPIError, LastFmAdapter

        response = Mock()
        response.json.return_value = {"error": 6, "message": "Invalid parameters"}
        adapter = LastFmAdapter("test-key", request_get=Mock(return_value=response))

        with self.assertRaises(LastFmAPIError) as raised:
            adapter.get_track(artist="Artist", track="Track")

        self.assertEqual(raised.exception.code, 6)


class MusicBrainzAdapterTest(unittest.TestCase):
    def test_import_does_not_make_network_requests(self):
        """Importing the adapter must not contact MusicBrainz."""
        with patch("requests.get", side_effect=AssertionError("unexpected request")):
            from metadata_sources.musicbrainz import MusicBrainzAdapter

            MusicBrainzAdapter("Spoty/1.0 (tests)")

    def test_find_recordings_by_isrc_returns_validated_payload(self):
        """Find and validate MusicBrainz recordings by their ISRC."""
        from metadata_sources.musicbrainz import MusicBrainzAdapter

        payload = {"recordings": [{"id": "recording-id", "title": "Track"}]}
        response = Mock(status_code=200)
        response.json.return_value = payload
        request_get = Mock(return_value=response)
        adapter = MusicBrainzAdapter(
            "Spoty/1.0 (tests)", request_get=request_get, monotonic=lambda: 0
        )

        result = adapter.find_recordings_by_isrc("USRC17607839")

        self.assertEqual(result, payload)
        request_get.assert_called_once_with(
            "https://musicbrainz.org/ws/2/isrc/USRC17607839",
            params={"fmt": "json"},
            headers={"User-Agent": "Spoty/1.0 (tests)"},
            timeout=10,
        )

    def test_missing_isrc_match_is_not_a_provider_failure(self):
        """Represent an unknown ISRC as an empty match list."""
        from metadata_sources.musicbrainz import MusicBrainzAdapter

        response = Mock(status_code=404)
        request_get = Mock(return_value=response)
        adapter = MusicBrainzAdapter(
            "Spoty/1.0 (tests)", request_get=request_get, monotonic=lambda: 0
        )

        self.assertEqual(adapter.find_recordings_by_isrc("USRC17607839"), {"recordings": []})
        response.raise_for_status.assert_not_called()

    def test_search_recordings_escapes_terms_and_validates_limit(self):
        """Search by explicit title and artist while escaping query syntax."""
        from metadata_sources.musicbrainz import MusicBrainzAdapter

        response = Mock(status_code=200)
        response.json.return_value = {"recordings": [{"id": "recording-id"}]}
        request_get = Mock(return_value=response)
        adapter = MusicBrainzAdapter(
            "Spoty/1.0 (tests)", request_get=request_get, monotonic=lambda: 0
        )

        adapter.search_recordings('A "Title"', "Artist", limit=5)

        self.assertEqual(
            request_get.call_args.kwargs["params"],
            {
                "fmt": "json",
                "limit": "5",
                "query": 'recording:"A \\\"Title\\\"" AND artist:"Artist"',
            },
        )
        with self.assertRaises(ValueError):
            adapter.search_recordings("Title", "Artist", limit=0)

    def test_get_entity_rejects_invalid_kind_and_mismatched_response(self):
        """Reject unsupported entity paths and an unexpected returned MBID."""
        from metadata_sources.musicbrainz import MusicBrainzAdapter

        adapter = MusicBrainzAdapter("Spoty/1.0 (tests)", request_get=Mock())
        with self.assertRaises(ValueError):
            adapter.get_entity("invalid", "recording-id")

        response = Mock(status_code=200)
        response.json.return_value = {"id": "another-recording"}
        adapter = MusicBrainzAdapter(
            "Spoty/1.0 (tests)", request_get=Mock(return_value=response), monotonic=lambda: 0
        )
        with self.assertRaises(ValueError):
            adapter.get_entity("recording", "recording-id")


    def test_retries_transient_failures_with_backoff_and_retry_after(self):
        """Retry temporary HTTP failures without ignoring provider backpressure."""
        import requests
        from metadata_sources.musicbrainz import MusicBrainzAdapter

        failure = Mock(status_code=503, headers={"Retry-After": "5"})
        failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
        success = Mock(status_code=200)
        success.json.return_value = {"recordings": []}
        request = Mock(side_effect=[failure, requests.Timeout(), success])
        sleep = Mock()
        adapter = MusicBrainzAdapter("Spoty tests", request_get=request, sleep=sleep, monotonic=lambda: 0)
        self.assertEqual(adapter.search_recordings("Title", "Artist"), {"recordings": []})
        self.assertEqual(request.call_count, 3)
        self.assertIn(unittest.mock.call(5.0), sleep.call_args_list)
        self.assertIn(unittest.mock.call(4.0), sleep.call_args_list)

    def test_retries_are_bounded_and_permanent_errors_fail_immediately(self):
        """Stop after the retry budget and never retry permanent client errors."""
        import requests
        from metadata_sources.musicbrainz import MusicBrainzAdapter

        for status, calls, headers in ((503, 4, {}), (400, 1, {}), (429, 1, {"Retry-After": "120"})):
            with self.subTest(status=status):
                response = Mock(status_code=status, headers=headers)
                response.raise_for_status.side_effect = requests.HTTPError(response=response)
                request = Mock(return_value=response)
                adapter = MusicBrainzAdapter("Spoty tests", request_get=request, sleep=Mock(), monotonic=lambda: 0)
                with self.assertRaises(requests.HTTPError):
                    adapter.search_recordings("Title", "Artist")
                self.assertEqual(request.call_count, calls)


class AcousticBrainzAdapterTest(unittest.TestCase):
    def test_import_does_not_make_network_requests(self):
        """Importing the adapter must not contact AcousticBrainz."""
        with patch("requests.get", side_effect=AssertionError("unexpected request")):
            from metadata_sources.acousticbrainz import AcousticBrainzAdapter

            AcousticBrainzAdapter()

    def test_get_analysis_validates_level_and_returns_payload(self):
        """Fetch a valid analysis using a MusicBrainz recording ID."""
        from metadata_sources.acousticbrainz import AcousticBrainzAdapter

        payload = {"highlevel": {"genre": {"value": "rock"}}}
        response = Mock(status_code=200)
        response.json.return_value = payload
        request_get = Mock(return_value=response)

        result = AcousticBrainzAdapter(request_get=request_get).get_analysis(
            "recording-id", "high-level"
        )

        self.assertEqual(result, payload)
        request_get.assert_called_once_with(
            "https://acousticbrainz.org/api/v1/recording-id/high-level",
            timeout=10,
        )

    def test_missing_analysis_returns_none_and_invalid_json_fails(self):
        """Distinguish absent provider data from malformed successful responses."""
        from metadata_sources.acousticbrainz import AcousticBrainzAdapter

        missing = Mock(status_code=404)
        adapter = AcousticBrainzAdapter(request_get=Mock(return_value=missing))
        self.assertIsNone(adapter.get_analysis("recording-id", "low-level"))

        malformed = Mock(status_code=200)
        malformed.json.return_value = {"highlevel": None}
        adapter = AcousticBrainzAdapter(request_get=Mock(return_value=malformed))
        with self.assertRaises(ValueError):
            adapter.get_analysis("recording-id", "high-level")


if __name__ == "__main__":
    unittest.main()
