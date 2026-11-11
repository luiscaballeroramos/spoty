"""Orchestrate optional metadata lookups without holding DB transactions over HTTP."""

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable
import requests

from metadata_sources.acousticbrainz import AcousticBrainzAdapter
from metadata_sources.lastfm import LastFmAPIError, LastFmAdapter
from metadata_sources.musicbrainz import MusicBrainzAdapter
from register.enrichment import MetadataRepository
from services.enrichment_contract import EnrichmentReport


PROVIDERS = ("all", "lastfm", "musicbrainz", "acousticbrainz")


@dataclass(frozen=True)
class EnrichmentResult:
    candidate_tracks: int
    records_saved: int
    matched: dict[str, int] = field(default_factory=dict)
    not_found: dict[str, int] = field(default_factory=dict)
    ambiguous_musicbrainz: int = 0
    provider_errors: dict[str, int] = field(default_factory=dict)
    provider_error_details: dict[str, dict[str, int]] = field(default_factory=dict)
    provider: str = "all"

    @property
    def exit_code(self) -> int:
        return self.to_report().exit_code

    def to_report(self) -> EnrichmentReport:
        errors = {provider: dict(details) for provider, details in self.provider_error_details.items()}
        for provider, count in self.provider_errors.items():
            details = errors.setdefault(provider, {})
            unknown = count - sum(details.values())
            if unknown > 0:
                details["unclassified_error"] = unknown
        return EnrichmentReport(self.provider,
            "partial" if any(self.provider_errors.values()) else "completed",
            {"track": self.candidate_tracks}, self.records_saved, errors,
            {"matched": self.matched, "not_found": self.not_found,
             "ambiguous_musicbrainz": self.ambiguous_musicbrainz})

    def to_json(self) -> str:
        return self.to_report().to_json()


def _error_category(error: Exception) -> str:
    if isinstance(error, requests.HTTPError):
        status = getattr(getattr(error, "response", None), "status_code", None)
        return f"http_{status}" if isinstance(status, int) else "http_error"
    if isinstance(error, requests.Timeout):
        return "timeout"
    if isinstance(error, requests.ConnectionError):
        return "connection_error"
    if isinstance(error, LastFmAPIError):
        return f"api_{error.code}"
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return "invalid_response"
    return "unexpected_error"


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(
        "".join(char if char.isalnum() else " " for char in without_marks).split()
    )


def _named_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item["name"]
        for item in value
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_lastfm(payload: dict[str, Any]) -> dict[str, Any]:
    track = payload["track"]
    artist = track.get("artist")
    album = track.get("album")
    artist_name = artist.get("name") if isinstance(artist, dict) else artist
    album_name = album.get("title") if isinstance(album, dict) else None
    album_images = album.get("image") if isinstance(album, dict) else None
    tags_container = track.get("toptags")
    tags = _named_items(tags_container.get("tag")) if isinstance(tags_container, dict) else []
    image_urls = [
        image.get("#text")
        for image in album_images
        if isinstance(image, dict) and image.get("#text")
    ] if isinstance(album_images, list) else []
    return {
        "provider_track_id": track.get("id"),
        "provider_mbid": track.get("mbid") or None,
        "title": track["name"],
        "artist_name": artist_name if isinstance(artist_name, str) else None,
        "album_name": album_name if isinstance(album_name, str) else None,
        "url": track.get("url"),
        "duration_ms": _as_int(track.get("duration")),
        "album_images": image_urls,
        "tags": tags,
        "wiki": track.get("wiki") if isinstance(track.get("wiki"), dict) else None,
    }


def _normalize_musicbrainz(recording: dict[str, Any]) -> dict[str, Any]:
    credits = recording.get("artist-credit")
    normalized_credits = []
    if isinstance(credits, list):
        for credit in credits:
            if not isinstance(credit, dict):
                continue
            artist = credit.get("artist")
            normalized_credits.append({
                "name": credit.get("name"),
                "artist_mbid": artist.get("id") if isinstance(artist, dict) else None,
                "artist_name": artist.get("name") if isinstance(artist, dict) else None,
                "joinphrase": credit.get("joinphrase"),
            })
    releases = recording.get("releases")
    normalized_releases = [
        {
            "mbid": release.get("id"),
            "title": release.get("title"),
            "date": release.get("date"),
        }
        for release in releases
        if isinstance(release, dict)
    ] if isinstance(releases, list) else []
    return {
        "recording_mbid": recording["id"],
        "title": recording["title"],
        "length_ms": _as_int(recording.get("length")),
        "first_release_date": recording.get("first-release-date"),
        "artist_credit": normalized_credits,
        "releases": normalized_releases,
        "genres": _named_items(recording.get("genres")),
        "tags": _named_items(recording.get("tags")),
    }


def _normalize_acousticbrainz(
    payload: dict[str, Any], level: str
) -> dict[str, Any]:
    response_key = "lowlevel" if level == "low-level" else "highlevel"
    return {response_key: payload[response_key]}


def _musicbrainz_matches(
    response: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    recordings = response.get("recordings")
    if not isinstance(recordings, list):
        raise ValueError("MusicBrainz returned an invalid recording search.")

    expected_title = _normalized_text(candidate["title"])
    expected_artist = _normalized_text(candidate["artist_name"])
    expected_duration = _as_int(candidate.get("duration_ms"))
    matches = []
    for recording in recordings:
        if not isinstance(recording, dict) or not isinstance(recording.get("id"), str):
            continue
        if _normalized_text(str(recording.get("title") or "")) != expected_title:
            continue
        credits = recording.get("artist-credit")
        if not isinstance(credits, list):
            continue
        artist_names = []
        for credit in credits:
            if not isinstance(credit, dict):
                continue
            artist = credit.get("artist")
            if isinstance(artist, dict) and isinstance(artist.get("name"), str):
                artist_names.append(artist["name"])
            if isinstance(credit.get("name"), str):
                artist_names.append(credit["name"])
        if expected_artist not in {_normalized_text(name) for name in artist_names}:
            continue
        length = _as_int(recording.get("length"))
        if (
            expected_duration is not None
            and length is not None
            and abs(expected_duration - length) > 5000
        ):
            continue
        matches.append(recording)
    return matches


class EnrichmentService:
    """Enrich stored Spotify tracks with provider-owned metadata records."""

    def __init__(
        self,
        database_url: str,
        lastfm_api_key: str | None = None,
        musicbrainz_user_agent: str = "Spoty/0.1.0 (metadata enrichment)",
        repository: Any | None = None,
        lastfm_factory: Callable[[str], Any] | None = None,
        musicbrainz_factory: Callable[[str], Any] | None = None,
        acousticbrainz_factory: Callable[[], Any] | None = None,
    ):
        self._database_url = database_url
        self._lastfm_api_key = lastfm_api_key
        self._musicbrainz_user_agent = musicbrainz_user_agent
        self._repository = repository or MetadataRepository()
        self._lastfm_factory = lastfm_factory or LastFmAdapter
        self._musicbrainz_factory = musicbrainz_factory or MusicBrainzAdapter
        self._acousticbrainz_factory = acousticbrainz_factory or AcousticBrainzAdapter

    def _selection(self, provider):
        if provider not in PROVIDERS:
            raise ValueError("Unknown metadata provider.")
        if provider == "lastfm" and not self._lastfm_api_key:
            raise ValueError("LASTFM_API_KEY is required for Last.fm enrichment.")
        include_lastfm = provider in ("all", "lastfm") and bool(self._lastfm_api_key)
        include_musicbrainz = provider in ("all", "musicbrainz")
        include_acousticbrainz = provider in ("all", "acousticbrainz")
        return include_lastfm, include_musicbrainz, include_acousticbrainz

    def get_candidates(self, limit: int | None = 50, *, provider: str = "all"):
        include_lastfm, include_musicbrainz, include_acousticbrainz = self._selection(provider)
        return self._repository.get_candidates(
            self._database_url,
            limit,
            include_lastfm=include_lastfm,
            include_musicbrainz=include_musicbrainz,
            include_acousticbrainz=include_acousticbrainz,
        )

    def enrich_pending(self, limit: int = 50, *, provider: str = "all") -> EnrichmentResult:
        candidates = self.get_candidates(limit, provider=provider)
        return self.enrich_candidates(candidates, provider=provider)

    def enrich_candidates(self, candidates, *, provider: str = "all") -> EnrichmentResult:
        include_lastfm, include_musicbrainz, include_acousticbrainz = self._selection(provider)
        if not candidates:
            return EnrichmentResult(0, 0, provider=provider)

        lastfm = self._lastfm_factory(self._lastfm_api_key) if include_lastfm else None
        musicbrainz = self._musicbrainz_factory(self._musicbrainz_user_agent) if include_musicbrainz else None
        acousticbrainz = self._acousticbrainz_factory() if include_acousticbrainz else None
        records: list[dict[str, Any]] = []
        matched = {"lastfm": 0, "musicbrainz": 0, "acousticbrainz": 0}
        not_found = {"lastfm": 0, "musicbrainz": 0, "acousticbrainz": 0}
        provider_errors = {"lastfm": 0, "musicbrainz": 0, "acousticbrainz": 0}
        provider_error_details: dict[str, dict[str, int]] = {}

        def record_error(provider, error):
            provider_errors[provider] += 1
            category = _error_category(error)
            details = provider_error_details.setdefault(provider, {})
            details[category] = details.get(category, 0) + 1

        ambiguous_musicbrainz = 0

        for candidate in candidates:
            track_id = candidate["spotify_track_id"]
            title = candidate["title"]
            artist = candidate["artist_name"]
            recording_mbid = candidate.get("recording_mbid")
            needs_acousticbrainz = {
                "low-level": candidate.get("needs_acousticbrainz_low_level", False),
                "high-level": candidate.get("needs_acousticbrainz_high_level", False),
            }

            if lastfm is not None and candidate.get("needs_lastfm", True):
                try:
                    payload = lastfm.get_track(artist=artist, track=title)
                except LastFmAPIError as error:
                    if error.code not in {6, 7}:
                        record_error("lastfm", error)
                    else:
                        records.append({
                            "provider": "lastfm",
                            "spotify_track_id": track_id,
                            "match_status": "not_found",
                            "raw_response": error.payload,
                        })
                        not_found["lastfm"] += 1
                except Exception as error:
                    record_error("lastfm", error)
                else:
                    track_data = payload["track"]
                    returned_artist = track_data.get("artist")
                    returned_artist = (
                        returned_artist.get("name")
                        if isinstance(returned_artist, dict)
                        else returned_artist
                    )
                    is_match = (
                        _normalized_text(str(track_data.get("name") or ""))
                        == _normalized_text(title)
                        and isinstance(returned_artist, str)
                        and _normalized_text(returned_artist) == _normalized_text(artist)
                    )
                    if is_match:
                        records.append({
                            "provider": "lastfm",
                            "spotify_track_id": track_id,
                            "match_status": "matched",
                            "normalized_data": _normalize_lastfm(payload),
                            "raw_response": payload,
                        })
                        matched["lastfm"] += 1
                    else:
                        records.append({
                            "provider": "lastfm",
                            "spotify_track_id": track_id,
                            "match_status": "not_found",
                            "raw_response": payload,
                        })
                        not_found["lastfm"] += 1

            if musicbrainz is not None and candidate.get("needs_musicbrainz", True):
                try:
                    search_response = musicbrainz.search_recordings(title, artist)
                    source_matches = _musicbrainz_matches(search_response, candidate)
                    recording = None
                    if len(source_matches) == 1:
                        search_match = source_matches[0]
                        recording = musicbrainz.get_entity(
                            "recording",
                            search_match["id"],
                            includes=(
                                "artists+releases+release-groups+isrcs+tags+genres+"
                                "aliases+url-rels"
                            ),
                        )
                        normalized_recording = _normalize_musicbrainz(recording)
                except Exception as error:
                    record_error("musicbrainz", error)
                    continue
                if len(source_matches) == 1:
                    recording_mbid = recording["id"]
                    records.append({
                        "provider": "musicbrainz",
                        "spotify_track_id": track_id,
                        "match_status": "matched",
                        "recording_mbid": recording_mbid,
                        "normalized_data": normalized_recording,
                        "raw_response": search_response,
                    })
                    matched["musicbrainz"] += 1
                    needs_acousticbrainz = {
                        "low-level": True,
                        "high-level": True,
                    }
                else:
                    status = "ambiguous" if source_matches else "not_found"
                    records.append({
                        "provider": "musicbrainz",
                        "spotify_track_id": track_id,
                        "match_status": status,
                        "raw_response": search_response,
                    })
                    if source_matches:
                        ambiguous_musicbrainz += 1
                    else:
                        not_found["musicbrainz"] += 1
                    recording_mbid = None

            if acousticbrainz is not None and recording_mbid:
                for level, response_key in (
                    ("low-level", "lowlevel"),
                    ("high-level", "highlevel"),
                ):
                    if not needs_acousticbrainz[level]:
                        continue
                    try:
                        payload = acousticbrainz.get_analysis(recording_mbid, level)
                    except Exception as error:
                        record_error("acousticbrainz", error)
                        continue
                    analysis = payload.get(response_key) if payload else None
                    if analysis is None:
                        records.append({
                            "provider": "acousticbrainz",
                            "spotify_track_id": track_id,
                            "recording_mbid": recording_mbid,
                            "analysis_level": level,
                            "match_status": "not_found",
                        })
                        not_found["acousticbrainz"] += 1
                    else:
                        records.append({
                            "provider": "acousticbrainz",
                            "spotify_track_id": track_id,
                            "recording_mbid": recording_mbid,
                            "analysis_level": level,
                            "match_status": "found",
                            "normalized_data": _normalize_acousticbrainz(payload, level),
                            "raw_response": payload,
                        })
                        matched["acousticbrainz"] += 1

        records_saved = self._repository.save_results(self._database_url, records)
        return EnrichmentResult(
            candidate_tracks=len(candidates),
            records_saved=records_saved,
            matched=matched,
            not_found=not_found,
            ambiguous_musicbrainz=ambiguous_musicbrainz,
            provider_errors=provider_errors,
            provider_error_details=provider_error_details,
            provider=provider,
        )
