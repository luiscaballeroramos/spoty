import base64
import hashlib
import json
import unittest
from unittest.mock import Mock

from spoty_data_reader import DataReadError, GitHubDataReader, parse_csv


TRACK_ID = "A" * 22
ARTIST_ID = "B" * 22
ALBUM_ID = "C" * 22
COMMIT = "1" * 40
TREE_SHA = "2" * 40

CSV_DATA = {
    "listening_events": (
        b"schema_version,track_id,played_at,context_source,context_type,context_id\n"
        + f"1,{TRACK_ID},1700000000,\\N,\\N,\\N\n".encode()
    ),
    "tracks": (
        b"id,name,duration_ms,album_id,album_track,artists_ids,explicit,popularity,disc_number\n"
        + f'{TRACK_ID},Example,180000,{ALBUM_ID},1,"[""{ARTIST_ID}""]",0,50,1\n'.encode()
    ),
    "artists": f"id,name\n{ARTIST_ID},Artist\n".encode(),
    "albums": (
        b"id,name,artists,tracks,total_tracks,images,release_year\n"
        + f'{ALBUM_ID},Album,"[""{ARTIST_ID}""]","[""{TRACK_ID}""]",1,[],2020\n'.encode()
    ),
}


def git_blob(data):
    sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    return sha, {
        "sha": sha,
        "size": len(data),
        "encoding": "base64",
        "content": base64.b64encode(data).decode(),
    }


class GitHubDataReaderTests(unittest.TestCase):
    def test_reads_all_files_from_the_resolved_commit(self):
        blobs = {name: git_blob(content) for name, content in CSV_DATA.items()}
        tree_entries = [
            {"path": f"{name}.csv", "type": "blob", "sha": blob[0]}
            for name, blob in blobs.items()
        ]
        responses = {
            f"/commits/main": {
                "sha": COMMIT,
                "commit": {"tree": {"sha": TREE_SHA}},
            },
            f"/git/trees/{TREE_SHA}?recursive=1": {
                "sha": TREE_SHA, "truncated": False, "tree": tree_entries,
            },
        }
        responses.update({
            f"/git/blobs/{sha}": blob
            for sha, blob in blobs.values()
        })
        request = Mock(side_effect=lambda url, **kwargs: Mock(
            status_code=200,
            json=lambda: responses[url.split("/repos/luiscaballeroramos/spoty-data", 1)[1]],
        ))

        snapshot = GitHubDataReader(token="read-only-token", request=request).load()

        self.assertEqual(snapshot.commit, COMMIT)
        self.assertEqual(len(snapshot.listening_events), 1)
        self.assertEqual(snapshot.tracks[0]["artists_ids"], [ARTIST_ID])
        self.assertEqual(snapshot.artists[0]["name"], "Artist")
        self.assertEqual(snapshot.albums[0]["tracks"], [TRACK_ID])
        self.assertTrue(all(call.kwargs["headers"]["Authorization"] == "Bearer read-only-token"
                            for call in request.call_args_list))

    def test_rejects_duplicate_listening_event_keys(self):
        duplicate = (
            CSV_DATA["listening_events"]
            + f"1,{TRACK_ID},1700000000,\\N,\\N,\\N\n".encode()
        )
        with self.assertRaisesRegex(DataReadError, "claves duplicadas"):
            parse_csv("listening_events", duplicate)

    def test_rejects_unexpected_csv_header(self):
        with self.assertRaisesRegex(DataReadError, "encabezado"):
            parse_csv("artists", b"id,name,extra\n")

    def test_rejects_blob_content_that_does_not_match_its_git_hash(self):
        blob_sha, blob = git_blob(CSV_DATA["artists"])
        blob["content"] = base64.b64encode(b"tampered").decode()
        request = Mock(return_value=Mock(status_code=200, json=lambda: blob))
        reader = GitHubDataReader(request=request)
        with self.assertRaisesRegex(DataReadError, "integridad"):
            reader._read_blob(blob_sha)

    def test_reports_github_status_without_response_body(self):
        request = Mock(return_value=Mock(status_code=404, json=lambda: {"message": "private details"}))
        with self.assertRaisesRegex(DataReadError, "HTTP 404") as error:
            GitHubDataReader(request=request)._get_json("/commits/main")
        self.assertNotIn("private details", str(error.exception))


if __name__ == "__main__":
    unittest.main()
