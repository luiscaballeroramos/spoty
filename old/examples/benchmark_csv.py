"""Synthetic local CSV/Git measurements; never opens existing data repositories."""

import argparse
import ctypes
import gc
import hashlib
import json
import os
import platform
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from spoty.domain.listening import ListeningBatch, ValidationError
from spoty.domain.metadata import MetadataBatch
from spoty.services.consolidation import consolidate
from spoty.services.metadata import audio_projection
from spoty.services.summary_queries import SummaryQueryService
from spoty.storage.csv_codec import MAX_CSV_BYTES, SCHEMAS
from spoty.storage.local_git import LocalGitStore
from spoty.storage.snapshots import Snapshot, snapshot_changes


ACCOUNT = "synthetic-benchmark"
DATE = "2026-11-09T10:00:00Z"


@dataclass(frozen=True)
class Profile:
    tracks: int = 7297
    events: int = 11071
    likes: int = 3004
    raw_bytes_per_track: int = 0
    incremental_events: int = 50

    def validate(self):
        if any(type(value) is not int or value < 0 for value in asdict(self).values()):
            raise ValidationError("Benchmark sizes must be nonnegative integers.")
        if not self.tracks or not self.incremental_events or self.likes > self.tracks:
            raise ValidationError("Require tracks/incremental events > 0 and likes <= tracks.")
        if self.raw_bytes_per_track > MAX_CSV_BYTES // 2:
            raise ValidationError("Synthetic raw payload is too large for this benchmark.")


def identifier(prefix: str, index: int) -> str:
    return prefix + str(index).zfill(21)


def metadata(entity: str, entity_id: str, values: dict, raw: dict) -> dict:
    return {
        f"spotify_{entity}_id": entity_id, "normalized_data": values,
        "field_metadata": {}, "raw_response": raw, "missing_requested": [],
        "captured_at": DATE, "last_attempt_status": "matched", "last_attempt_at": DATE,
        "last_success_at": DATE, "next_attempt_at": None, "last_error_code": None,
        "last_success_run_id": "synthetic",
    }


def synthetic_rows(profile: Profile) -> dict:
    profile.validate()
    rows = {dataset: [] for dataset in SCHEMAS}
    artists = max(1, (profile.tracks + 2) // 3)
    albums = max(1, (profile.tracks + 9) // 10)
    rows["artists"] = [{"id": identifier("R", index), "name": f"Synthetic artist {index}"}
                       for index in range(artists)]
    rows["albums"] = [{
        "id": identifier("A", index), "name": f"Synthetic album {index}",
        "artists": [identifier("R", index % artists)], "tracks": None,
        "total_tracks": 10, "images": [], "release_year": 2020,
    } for index in range(albums)]
    for index in range(profile.tracks):
        track_id = identifier("T", index)
        rows["tracks"].append({
            "id": track_id, "name": f"Synthetic track {index}", "duration_ms": 180000,
            "album_id": identifier("A", index // 10), "album_track": index % 10 + 1,
            "disc_number": 1, "artists_ids": [identifier("R", index % artists)],
            "explicit": 0, "popularity": index % 101,
        })
        # Deterministic varied text avoids the unrealistically tiny Git objects
        # obtained by repeating one padding character across every row.
        raw = "".join(hashlib.sha256(f"{index}:{part}".encode()).hexdigest()
                      for part in range((profile.raw_bytes_per_track + 63) // 64))
        rows["smarterplaylists_track_metadata"].append(metadata(
            "track", track_id, {"name": f"Synthetic track {index}", "energy": 0.5},
            {"synthetic": raw[:profile.raw_bytes_per_track]},
        ))
    rows["smarterplaylists_artist_metadata"] = [
        metadata("artist", identifier("R", index), {"genres": ["folk rock", "rock"]}, {})
        for index in range(artists)
    ]
    rows["listening_events"] = [{
        "track_id": identifier("T", index % profile.tracks),
        "played_at": 1760000000 + index * 180,
        "context_source": None, "context_type": None, "context_id": None,
    } for index in range(profile.events)]
    rows["liked_tracks"] = [{"id": identifier("T", index)} for index in range(profile.likes)]
    rows["favorites_replica_state"] = [{"singleton_id": 1, "applied_revision": 0}]
    rows["genres"] = [{
        "id": genre_id, "name": name, "introduced_by": "synthetic", "created_at": DATE,
    } for genre_id, name in (("folk", "Folk"), ("rock", "Rock"), ("folk-rock", "Folk Rock"))]
    rows["genre_relations"] = [{
        "parent_id": parent, "child_id": "folk-rock", "introduced_by": "synthetic",
    } for parent in ("folk", "rock")]
    rows["genre_label_mappings"] = [{
        "source": "smarterplaylists", "field": "genres", "normalized_label": label,
        "genre_id": genre_id, "status": "approved", "introduced_by": "synthetic", "created_at": DATE,
    } for label, genre_id in (("folk rock", "folk-rock"), ("rock", "rock"))]
    maps = {dataset: {tuple(row[key] for key in SCHEMAS[dataset].keys): row for row in entries}
            for dataset, entries in rows.items()}
    rows["track_audio"] = list(audio_projection(maps).values())
    return rows


def windows_memory() -> tuple[int, int]:
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            *[(name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage",
            )],
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize, counters.PeakWorkingSetSize


@contextmanager
def measure(report: dict, name: str):
    gc.collect()
    before, _ = windows_memory()
    samples = [before]
    failures = []
    stopped = threading.Event()

    def sample():
        try:
            while not stopped.wait(0.05):
                samples.append(windows_memory()[0])
        except OSError as error:
            failures.append(error)

    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        stopped.set()
        worker.join()
        current, peak = windows_memory()
        samples.append(current)
        report[name] = {
            "seconds": round(elapsed, 4), "rss_before_bytes": before,
            "rss_after_bytes": current, "sampled_rss_peak_bytes": max(samples),
            "process_lifetime_rss_peak_bytes": peak,
        }
    if failures:
        raise failures[0]


class MeasuredStore(LocalGitStore):
    def __init__(self, path):
        super().__init__(path)
        self.git_calls = 0
        self.read_bytes = 0

    def _git(self, *args, **kwargs):
        if hasattr(self, "git_calls"):
            self.git_calls += 1
        return super()._git(*args, **kwargs)

    def read(self, commit, path):
        content = super().read(commit, path)
        self.read_bytes += len(content)
        return content

    def read_many(self, commit, paths):
        content = super().read_many(commit, paths)
        self.read_bytes += sum(len(data) for data in content)
        return content


def storage_sizes(store: LocalGitStore) -> dict:
    files = [path for path in store.path.rglob("*") if path.is_file()]
    objects = store._git("count-objects", "-v").decode("ascii").splitlines()
    return {
        "repository_bytes": sum(path.stat().st_size for path in files),
        "git_objects": {key: int(value) for key, value in (line.split(": ") for line in objects)
                        if key in ("count", "size", "in-pack", "packs", "size-pack")},
    }


def incremental_batch(profile: Profile, *, new_tracks: bool = False) -> ListeningBatch:
    return ListeningBatch.from_items(ACCOUNT, "synthetic-benchmark", DATE, [{
        "played_at": "2026-11-09T10:00:00Z",
        "track": {
            "id": identifier("T", profile.tracks + index if new_tracks else index % profile.tracks),
            "name": "Synthetic track",
            "duration_ms": 180000,
            "artists": [{"id": identifier("R", 0), "name": "Synthetic artist"}],
            "album": {"id": identifier("A", 0), "name": "Synthetic album", "total_tracks": 10,
                      "artists": [{"id": identifier("R", 0), "name": "Synthetic artist"}]},
        },
        "context": None,
    } for index in range(profile.incremental_events)])


def metadata_batch(profile: Profile, *, smarterplaylists: bool = False) -> MetadataBatch:
    if not smarterplaylists:
        return MetadataBatch.from_enrichment_records(ACCOUNT, "synthetic-benchmark", DATE, [{
            "provider": "lastfm", "spotify_track_id": identifier("T", index),
            "match_status": "matched", "normalized_data": {"name": f"Synthetic Last.fm {index}"},
            "raw_response": {"synthetic": True},
        } for index in range(profile.incremental_events)])
    captures = []
    for index in range(profile.incremental_events):
        track_id = identifier("T", index)
        values = {"id": track_id, "uri": f"spotify:track:{track_id}",
                  "name": f"Synthetic track {index}", "energy": 0.75, "tempo": 120}
        captures.append({
            "captured_at": "2026-11-09T11:00:00Z", "entities": {"track": {
                "values": values, "conflicts": {}, "missing_requested": [],
                "raw": {"status": "complete", "playlist_uri": None, "tracks": [values]},
            }},
        })
    return MetadataBatch.from_smarterplaylists(
        ACCOUNT, "synthetic-benchmark", "2026-11-09T11:00:00Z", captures,
    )


def run_benchmark(profile: Profile) -> dict:
    profile.validate()
    if os.name != "nt":
        raise RuntimeError("This benchmark currently requires Windows process-memory counters.")
    if profile.incremental_events > profile.tracks:
        raise ValidationError("Incremental events must not exceed tracks (one new event per track).")
    report = {
        "profile": asdict(profile), "platform": platform.platform(), "python": platform.python_version(),
        "measurement_notes": [
            "Synthetic data only; temporary bare Git is removed on exit.",
            "RSS samples every 50ms include Python only, not Git subprocess memory; brief peaks may be missed.",
            "Process lifetime RSS peak is cumulative, not attributable to a single phase.",
            "Local Git objects are loose; no gc/repack, network or Streamlit rendering is timed.",
            "One run per operation; OS disk cache is not cleared. Not a cloud performance certification.",
            "Raw payload is deterministic SHA-256 hexadecimal text, not a real provider response.",
        ],
        "phases": {},
    }
    phases = report["phases"]
    with TemporaryDirectory(prefix="spoty-benchmark-") as directory:
        initial = LocalGitStore.initialize(Path(directory) / "data.git")
        store = MeasuredStore(initial.path)
        with measure(phases, "generate"):
            rows = synthetic_rows(profile)
        with measure(phases, "initial_snapshot"):
            base = Snapshot.load(store, ACCOUNT)
            changes = snapshot_changes(base, rows, snapshot_id="a" * 32, published_at=DATE)
            report["initial_partition_bytes"] = sum(len(data) for path, data in changes.items()
                                                    if data is not None and "/datasets/" in path)
            store.publish(base.commit_id, changes, "Synthetic benchmark snapshot")
        del rows, changes
        report["storage_before"] = storage_sizes(store)
        with measure(phases, "load_snapshot"):
            snapshot = Snapshot.load(store, ACCOUNT)
            queries = SummaryQueryService(snapshot)
        results = {}
        operations = {
            "counts": queries.library_counts, "history": queries.recent_listening_events,
            "genres_labels": queries.top_listened_genres,
            "genres_families": lambda: queries.top_listened_genres(grouping="families"),
            "metadata_tracks": lambda: queries.metadata_overview("tracks"),
        }
        for name, operation in operations.items():
            calls, read = store.git_calls, store.read_bytes
            with measure(phases, name):
                results[name] = operation()
            phases[name].update(git_calls=store.git_calls - calls, read_bytes=store.read_bytes - read)
        expected = profile.events
        if results["counts"]["total_tracks"] != profile.tracks or results["counts"]["total_listening_events"] != expected:
            raise AssertionError("Synthetic catalog/history totals do not match profile.")
        if results["counts"]["total_liked_tracks"] != profile.likes:
            raise AssertionError("Synthetic favorites count does not match profile.")
        for name in ("genres_labels", "genres_families"):
            result = results[name]
            if result["classified_listens"] != expected or result["total_listens"] != expected:
                raise AssertionError("Synthetic genre coverage differs from generated listens.")
            if expected and (len(result["genres"]) != 2 or any(
                row["weighted_listens"] != expected or row["share_percent"] != 100
                for row in result["genres"]
            )):
                raise AssertionError("Overlapping synthetic genres must each receive all listens.")
        if len(results["history"]) != min(100, expected):
            raise AssertionError("Recent history limit does not match the generated profile.")
        if results["metadata_tracks"]["enriched"] != profile.tracks:
            raise AssertionError("Synthetic metadata coverage differs from catalog.")
        batch = incremental_batch(profile)
        with measure(phases, "publish_increment"):
            store.publish_batch(batch)
        if snapshot.pending_batches() != 0:
            raise AssertionError("Pinned snapshot unexpectedly observes new inbox.")
        calls, read = store.git_calls, store.read_bytes
        with measure(phases, "consolidate_increment"):
            result = consolidate(store, ACCOUNT)
        phases["consolidate_increment"].update(
            git_calls=store.git_calls - calls, read_bytes=store.read_bytes - read,
        )
        with measure(phases, "verify_increment"):
            after = Snapshot.load(store, ACCOUNT)
            after_counts = SummaryQueryService(after).library_counts()
            if (result.saved_events != profile.incremental_events or result.rejected_batches != 0
                    or after.pending_batches() != 0
                    or after_counts["total_listening_events"] != expected + profile.incremental_events
                    or queries.library_counts()["total_listening_events"] != expected):
                raise AssertionError("Incremental consolidation or historical snapshot invariants failed.")
            if SummaryQueryService(after).metadata_overview("tracks") != results["metadata_tracks"]:
                raise AssertionError("Listening-only consolidation changed metadata coverage.")
        report["storage_after"] = storage_sizes(store)
        report["repository_growth_bytes"] = (
            report["storage_after"]["repository_bytes"] - report["storage_before"]["repository_bytes"]
        )
        report["verified"] = {
            "counts": results["counts"], "incremental_saved_events": result.saved_events,
            "final_events": after_counts["total_listening_events"], "historical_snapshot_unchanged": True,
            "labels": results["genres_labels"]["genres"], "families": results["genres_families"]["genres"],
        }
        new_batch = incremental_batch(profile, new_tracks=True)
        with measure(phases, "publish_new_tracks"):
            store.publish_batch(new_batch)
        calls, read = store.git_calls, store.read_bytes
        with measure(phases, "consolidate_new_tracks"):
            new_result = consolidate(store, ACCOUNT)
        phases["consolidate_new_tracks"].update(
            git_calls=store.git_calls - calls, read_bytes=store.read_bytes - read,
        )
        with measure(phases, "verify_new_tracks"):
            final = Snapshot.load(store, ACCOUNT)
            final_queries = SummaryQueryService(final)
            final_counts = final_queries.library_counts()
            coverage = final_queries.metadata_overview("tracks")
            final_families = final_queries.top_listened_genres(grouping="families")
            final_total = expected + 2 * profile.incremental_events
            if (new_result.saved_events != profile.incremental_events
                    or new_result.accepted_batches != 1 or new_result.rejected_batches != 0
                    or final_counts["total_tracks"] != profile.tracks + profile.incremental_events
                    or final_counts["total_listening_events"] != final_total
                    or coverage["total"] != final_counts["total_tracks"]
                    or coverage["enriched"] != profile.tracks
                    or final_families["classified_listens"] != final_total
                    or final.pending_batches() != 0
                    or SummaryQueryService(after).library_counts() != after_counts
                    or queries.library_counts() != results["counts"]):
                raise AssertionError("New-track consolidation or historical snapshot invariants failed.")
        report["storage_after_new_tracks"] = storage_sizes(store)
        report["new_tracks_repository_growth_bytes"] = (
            report["storage_after_new_tracks"]["repository_bytes"] - report["storage_after"]["repository_bytes"]
        )
        report["verified"]["new_tracks"] = {
            "added_tracks": profile.incremental_events, "final_counts": final_counts,
            "enriched_tracks": coverage["enriched"], "classified_listens": final_families["classified_listens"],
            "previous_snapshots_unchanged": True,
        }
        for name, smarterplaylists in (("lastfm", False), ("smarterplaylists", True)):
            update = metadata_batch(profile, smarterplaylists=smarterplaylists)
            before_update = Snapshot.load(store, ACCOUNT)
            previous_coverage = SummaryQueryService(before_update).metadata_overview("tracks")
            with measure(phases, f"publish_{name}"):
                store.publish_batch(update)
            calls, read = store.git_calls, store.read_bytes
            with measure(phases, f"consolidate_{name}"):
                metadata_result = consolidate(store, ACCOUNT)
            phases[f"consolidate_{name}"].update(
                git_calls=store.git_calls - calls, read_bytes=store.read_bytes - read,
            )
            with measure(phases, f"verify_{name}"):
                updated = Snapshot.load(store, ACCOUNT)
                updated_queries = SummaryQueryService(updated)
                overview = updated_queries.metadata_overview("tracks")
                source = next(row for row in overview["sources"]
                              if row["source"] == ("SmarterPlaylists" if smarterplaylists else "Last.fm"))
                field = "tempo" if smarterplaylists else "name"
                if (metadata_result.saved_metadata != profile.incremental_events
                        or metadata_result.accepted_batches != 1 or metadata_result.rejected_batches != 0
                        or updated.pending_batches() != 0
                        or updated_queries.library_counts() != final_counts
                        or overview["enriched"] != profile.tracks
                        or {"field": field, "available": profile.incremental_events} not in source["fields"]
                        or SummaryQueryService(before_update).metadata_overview("tracks") != previous_coverage
                        or final_queries.library_counts() != final_counts):
                    raise AssertionError("Metadata update or historical snapshot invariants failed.")
                if smarterplaylists:
                    audio = {row["spotify_track_id"]: row for row in updated.rows("track_audio")}
                    for index in range(profile.incremental_events):
                        row = audio[identifier("T", index)]
                        if row["audio"] != {"energy": 0.75, "tempo": 120} or row["name"] != f"Synthetic track {index}":
                            raise AssertionError("Metadata update lost audio values or catalog names.")
            report["verified"][name] = {
                "saved_metadata": metadata_result.saved_metadata, "counts_unchanged": True,
                "coverage_verified": True, "previous_snapshot_unchanged": True,
            }
        report["storage_after_metadata"] = storage_sizes(store)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field, value in asdict(Profile()).items():
        parser.add_argument("--" + field.replace("_", "-"), type=int, default=value)
    parser.add_argument("--output", type=Path, help="Optional new JSON report file; refuses overwrite.")
    args = parser.parse_args()
    output = args.output
    if output is not None and output.exists():
        parser.error("--output must be a new file.")
    profile = Profile(**{key: getattr(args, key) for key in asdict(Profile())})
    report = run_benchmark(profile)
    text = json.dumps(report, indent=2) + "\n"
    if output is not None:
        with output.open("x", encoding="utf-8") as file:
            file.write(text)
    print(text)


if __name__ == "__main__":
    main()
