import time
from typing import Any

from config import VERBOSE
from spotifyapi.spotifyclient import SpotifyClient


def _pause() -> bool:
    """Pause the current Spotify playback."""
    try:
        client = SpotifyClient()
        client.sp.pause_playback()
        return True
    except Exception as exc:
        if VERBOSE:
            print(f"Error pausing playback: {exc}")
        return False


def _play() -> bool:
    """Resume Spotify playback."""
    try:
        client = SpotifyClient()
        client.sp.start_playback()
        return True
    except Exception as exc:
        if VERBOSE:
            print(f"Error resuming playback: {exc}")
        return False


def _log_error(action: str, exc: Exception) -> None:
    if VERBOSE:
        print(f"Error trying to {action}: {exc}")


def _next_track() -> bool:
    """Skip to the next track."""
    try:
        client = SpotifyClient()
        client.sp.next_track()
        return True
    except Exception as exc:
        _log_error("skip to next track", exc)
        return False


def _previous_track() -> bool:
    """Go back to the previous track."""
    try:
        client = SpotifyClient()
        client.sp.previous_track()
        return True
    except Exception as exc:
        _log_error("go to previous track", exc)
        return False


def _shuffle(state: bool) -> bool:
    """Enable or disable shuffle mode."""
    try:
        client = SpotifyClient()
        client.sp.shuffle(state)
        return True
    except Exception as exc:
        _log_error(f"set shuffle={state}", exc)
        return False


def _repeat(mode: str) -> bool:
    """Set repeat mode. Allowed values: off, track, context."""
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode not in {"off", "track", "context"}:
        if VERBOSE:
            print("Invalid repeat mode. Use: off, track, or context")
        return False

    try:
        client = SpotifyClient()
        client.sp.repeat(normalized_mode)
        return True
    except Exception as exc:
        _log_error(f"set repeat mode to {normalized_mode}", exc)
        return False


def _get_current_playback() -> dict[str, Any] | None:
    """Return complete playback state for the current user."""
    try:
        client = SpotifyClient()
        return client.sp.current_playback()
    except Exception as exc:
        _log_error("get current playback", exc)
        return None


def _add_to_queue(uri: str, device_id: str | None = None) -> bool:
    """Add a track or episode URI to the user's queue."""
    if not uri or not uri.strip():
        if VERBOSE:
            print("uri is required")
        return False

    try:
        client = SpotifyClient()
        client.sp.add_to_queue(uri=uri, device_id=device_id)
        return True
    except Exception as exc:
        _log_error(f"add uri to queue: {uri}", exc)
        return False


def _get_queue() -> dict[str, Any] | None:
    """Return the user's current playback queue."""
    try:
        client = SpotifyClient()
        return client.sp.queue()
    except Exception as exc:
        _log_error("get queue", exc)
        return None


def _like_track(track_id: str) -> bool:
    """Save a track in the current user's library."""
    if not track_id or not track_id.strip():
        if VERBOSE:
            print("track_id is required")
        return False

    try:
        client = SpotifyClient()
        client.sp.current_user_saved_tracks_add([track_id])
        return True
    except Exception as exc:
        _log_error(f"like track {track_id}", exc)
        return False


def _unlike_track(track_id: str) -> bool:
    """Remove a track from the current user's library."""
    if not track_id or not track_id.strip():
        if VERBOSE:
            print("track_id is required")
        return False

    try:
        client = SpotifyClient()
        client.sp.current_user_saved_tracks_delete([track_id])
        return True
    except Exception as exc:
        _log_error(f"unlike track {track_id}", exc)
        return False


def _is_track_liked(track_id: str) -> bool:
    """Check whether a track is saved in the current user's library."""
    if not track_id or not track_id.strip():
        if VERBOSE:
            print("track_id is required")
        return False

    try:
        client = SpotifyClient()
        response = client.sp.current_user_saved_tracks_contains([track_id])
        return bool(response and response[0])
    except Exception as exc:
        _log_error(f"check liked status for track {track_id}", exc)
        return False


# TODO: _seek(position_ms: int)
# TODO: _set_volume(volume_percent: int)
# TODO: _play_context(context_uri: str, offset: int | None = None, position_ms: int | None = None)
# TODO: _play_tracks(uris: list[str], position_ms: int | None = None)
# TODO: _get_devices()
# TODO: _transfer_playback(device_id: str, force_play: bool = False)
# TODO: _set_playback_device_and_play(device_id: str, context_uri: str)
# TODO: _get_liked_tracks_page(limit: int = 50, offset: int = 0)
# TODO: _get_recommendations(seed_tracks=None, seed_artists=None, seed_genres=None)
# TODO: _get_audio_features(track_id: str)
# TODO: _search_tracks(query: str, limit: int = 10)


if __name__ == "__main__":
    print("_pause:", _pause())
    time.sleep(5)
    print("_play:", _play())
