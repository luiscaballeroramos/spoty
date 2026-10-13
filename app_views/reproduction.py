import html
import json
import time
from dataclasses import replace
from pathlib import Path

import streamlit as st

from streamlit_lab.blocks import Block, Size
from streamlit_lab.layout import Group, Layout
from streamlit_lab.renderer import render_button, render_group, render_page_style
from streamlit_lab.styles import (
    BlockStyle,
    ButtonStyle,
    Insets,
    PageStyle,
)
from services.reproduction import ReproductionService
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_spotify_authorization_section,
)

PLAYBACK_REFRESH_MARGIN_MS = 2_000
PLAYBACK_FRAGMENT_INTERVAL_SECONDS = 1
PLAYBACK_PLAYING_POLL_SECONDS = 5
PLAYBACK_PAUSED_POLL_SECONDS = 5
PLAYBACK_RESTORE_AFTER_SECONDS = 60
PLAYBACK_CONTROL_REFRESH_SECONDS = 4
ADJACENT_TRACKS_POLL_SECONDS = 15
PLAYBACK_CACHE_KEY = "reproduction_playback_cache"
PAUSED_PLAYBACK_KEY = "reproduction_paused_playback"
PERSISTED_PLAYBACK_LOADED_KEY = "reproduction_persisted_playback_loaded"
PLAYBACK_STATE_PATH = Path(__file__).resolve().parents[1] / ".playback_state.json"
PLAYBACK_FORCE_REFRESH_UNTIL_KEY = "reproduction_force_refresh_until"
SPOTIFY_CLIENT_CACHE_KEY = "reproduction_spotify_client"
SPOTIFY_CLIENT_MAX_AGE_SECONDS = 50 * 60
LIKED_CACHE_KEY = "reproduction_liked_cache"
ADJACENT_TRACKS_CACHE_KEY = "reproduction_adjacent_tracks_cache"
ADJACENT_TRACK_HISTORY_KEY = "reproduction_adjacent_track_history"
ADJACENT_PREVIOUS_REQUEST_KEY = "reproduction_previous_request"
REPRODUCTION_COLUMN_RATIOS = (1, 2, 1)
REPRODUCTION_VIEWPORT_OFFSET_PX = 64
REPRODUCTION_TEXT_HEIGHT_PX = 64
REPRODUCTION_PAGE_STYLE = PageStyle(
    hide_header=True,
    content_padding=0,
    content_overflow="visible",
    app_overflow_y="auto",
    vertical_gap_px=0,
    horizontal_gap_px=0,
    element_margin_px=0,
    caption_margin_px=Insets(top=0, right=0, bottom=1.6, left=0),
)
REPRODUCTION_LAYOUT = Layout(
    rows=1,
    columns=3,
    flow="row",
    column_weights=REPRODUCTION_COLUMN_RATIOS,
    gap_px=0,
    horizontal="center",
    vertical="center",
    height=Size(100, "dvh", offset_px=REPRODUCTION_VIEWPORT_OFFSET_PX),
    portrait_row_weights=REPRODUCTION_COLUMN_RATIOS,
    collapse_on_portrait=True,
)
REPRODUCTION_TRACK_BLOCKS = [
    Block(
        key=f"reproduction-{track}-{part}",
        color="#ffffff",
        height=(
            Size(
                100,
                "dvh",
                offset_px=REPRODUCTION_VIEWPORT_OFFSET_PX,
            )
            if part == "image"
            else Size(REPRODUCTION_TEXT_HEIGHT_PX)
        ),
        portrait_height=(
            Size(
                100 * weight / sum(REPRODUCTION_COLUMN_RATIOS),
                "dvh",
                offset_px=(
                    REPRODUCTION_VIEWPORT_OFFSET_PX
                    * weight
                    / sum(REPRODUCTION_COLUMN_RATIOS)
                ),
            )
            if part == "image"
            else Size(REPRODUCTION_TEXT_HEIGHT_PX)
        ),
        style=BlockStyle(
            padding=0 if part == "image" else 2,
            background_opacity=1 if part == "image" else 0,
            radius=0,
            content_gap_px=0,
            content_horizontal="center",
            content_vertical="start" if part == "image" else "center",
        ),
    )
    for track, weight in zip(("previous", "current", "next"), REPRODUCTION_COLUMN_RATIOS)
    for part in ("image", "title")
]
REPRODUCTION_TRACK_GROUPS = tuple(
    Group(
        key=f"reproduction-{track}",
        children=(f"reproduction-{track}-image", f"reproduction-{track}-title"),
        layout=Layout(
            rows=2,
            columns=1,
            gap_px=0,
            horizontal="center",
            vertical="center",
        ),
    )
    for track in ("previous", "current", "next")
)
REPRODUCTION_GROUP = Group(
    key="reproduction-controls",
    children=REPRODUCTION_TRACK_GROUPS,
    layout=REPRODUCTION_LAYOUT,
)
REPRODUCTION_CONTROL_STYLE = ButtonStyle(
    color="white",
    border="none",
    padding="0 0.15rem",
    border_radius="0",
    width="100%",
    aspect_ratio="1 / 1",
    min_height="0",
    font_size="3rem",
    font_weight="900",
    text_align="center",
    fill_container=True,
)
REPRODUCTION_CORNER_ACTION_STYLE = ButtonStyle(
    color="white",
    border="none",
    border_radius="0",
    background="rgba(0, 0, 0, 0.5)",
    padding="0",
    width="48px",
    height="48px",
    aspect_ratio="1 / 1",
    min_height="48px",
    font_size="1.25rem",
    text_align="center",
)


def _load_persisted_playback() -> None:
    if st.session_state.get(PERSISTED_PLAYBACK_LOADED_KEY):
        return
    st.session_state[PERSISTED_PLAYBACK_LOADED_KEY] = True

    try:
        saved_state = json.loads(PLAYBACK_STATE_PATH.read_text(encoding="utf-8"))
        playback = saved_state.get("playback")
        if not isinstance(playback, dict) or not playback.get("item"):
            return

        paused_at_epoch = saved_state.get("paused_at_epoch")
        if isinstance(paused_at_epoch, (int, float)):
            paused_elapsed = max(0, time.time() - paused_at_epoch)
            playback["paused_at"] = time.monotonic() - paused_elapsed
        st.session_state[PAUSED_PLAYBACK_KEY] = playback
    except (OSError, json.JSONDecodeError, AttributeError):
        return


def _persist_playback(playback: dict) -> None:
    if not playback.get("item"):
        return

    snapshot = dict(playback)
    paused_at = snapshot.pop("paused_at", None)
    saved_state = {
        "playback": snapshot,
        "paused_at_epoch": (
            time.time() - (time.monotonic() - paused_at)
            if isinstance(paused_at, (int, float))
            else None
        ),
    }
    temporary_path = PLAYBACK_STATE_PATH.with_suffix(".tmp")
    try:
        temporary_path.write_text(
            json.dumps(saved_state, ensure_ascii=True), encoding="utf-8"
        )
        temporary_path.replace(PLAYBACK_STATE_PATH)
    except OSError:
        temporary_path.unlink(missing_ok=True)


def _clear_persisted_playback() -> None:
    try:
        PLAYBACK_STATE_PATH.unlink(missing_ok=True)
    except OSError:
        return


def _get_spotify_client() -> SpotifyClient:
    now = time.monotonic()
    cached = st.session_state.get(SPOTIFY_CLIENT_CACHE_KEY)
    if (
        cached
        and isinstance(cached["client"], SpotifyClient)
        and now - cached["created_at"] < SPOTIFY_CLIENT_MAX_AGE_SECONDS
    ):
        return cached["client"]

    client = SpotifyClient()
    st.session_state[SPOTIFY_CLIENT_CACHE_KEY] = {
        "client": client,
        "created_at": now,
    }
    return client


def _copy_playback_with_estimated_progress(
    playback: dict, fetched_at: float, now: float
) -> dict:
    estimated_playback = dict(playback)
    if not playback.get("is_playing"):
        return estimated_playback

    item = playback.get("item") or {}
    progress_ms = playback.get("progress_ms")
    duration_ms = item.get("duration_ms")
    if not isinstance(progress_ms, (int, float)) or not isinstance(
        duration_ms, (int, float)
    ):
        return estimated_playback

    elapsed_ms = (now - fetched_at) * 1000
    estimated_playback["progress_ms"] = min(
        int(progress_ms + elapsed_ms), int(duration_ms)
    )
    return estimated_playback


def _get_playback(client: SpotifyClient) -> dict:
    _load_persisted_playback()
    now = time.monotonic()
    force_refresh_until = st.session_state.get(PLAYBACK_FORCE_REFRESH_UNTIL_KEY, 0)
    should_force_refresh = now < force_refresh_until
    if force_refresh_until and not should_force_refresh:
        st.session_state.pop(PLAYBACK_FORCE_REFRESH_UNTIL_KEY, None)

    saved_playback = st.session_state.get(PAUSED_PLAYBACK_KEY)
    cached = st.session_state.get(PLAYBACK_CACHE_KEY)
    if cached and not should_force_refresh:
        playback = cached["playback"]
        item = playback.get("item") or {}
        progress_ms = playback.get("progress_ms")
        duration_ms = item.get("duration_ms")
        elapsed_ms = (now - cached["fetched_at"]) * 1000

        if not playback.get("is_playing"):
            if elapsed_ms < PLAYBACK_PAUSED_POLL_SECONDS * 1000:
                return (
                    playback
                    if playback.get("item")
                    else (
                        {**saved_playback, "is_playing": False}
                        if saved_playback
                        else {}
                    )
                )
        elif (
            isinstance(progress_ms, (int, float))
            and isinstance(duration_ms, (int, float))
            and elapsed_ms < PLAYBACK_PLAYING_POLL_SECONDS * 1000
            and progress_ms + elapsed_ms + PLAYBACK_REFRESH_MARGIN_MS < duration_ms
        ):
            return _copy_playback_with_estimated_progress(
                playback, cached["fetched_at"], now
            )
        elif not isinstance(progress_ms, (int, float)) or not isinstance(
            duration_ms, (int, float)
        ):
            return playback

    playback = client.get_current_playback() or {}
    if saved_playback and playback.get("item"):
        if playback["item"].get("id") != (saved_playback.get("item") or {}).get(
            "id"
        ) or playback.get("is_playing"):
            st.session_state.pop(PAUSED_PLAYBACK_KEY, None)
            saved_playback = None
    if playback.get("item"):
        saved_item_id = (saved_playback or {}).get("item", {}).get("id")
        if (
            not playback.get("is_playing")
            and saved_playback
            and saved_item_id == playback["item"].get("id")
        ):
            if saved_playback.get("paused_at") is not None:
                playback["paused_at"] = saved_playback["paused_at"]
            if saved_playback.get("queue_uris"):
                playback["queue_uris"] = saved_playback["queue_uris"]
        elif not playback.get("is_playing"):
            playback["paused_at"] = now
        _persist_playback(playback)
    st.session_state[PLAYBACK_CACHE_KEY] = {
        "playback": playback,
        "fetched_at": now,
    }
    if not playback.get("item") and saved_playback:
        return {**saved_playback, "is_playing": False}
    return playback


def _invalidate_playback_cache() -> None:
    st.session_state.pop(PLAYBACK_CACHE_KEY, None)


def _request_playback_refresh_window() -> None:
    st.session_state[PLAYBACK_FORCE_REFRESH_UNTIL_KEY] = (
        time.monotonic() + PLAYBACK_CONTROL_REFRESH_SECONDS
    )


def refresh_playback_on_page_entry() -> None:
    _invalidate_playback_cache()
    st.session_state.pop(ADJACENT_TRACKS_CACHE_KEY, None)
    st.session_state.pop(ADJACENT_TRACK_HISTORY_KEY, None)
    st.session_state.pop(ADJACENT_PREVIOUS_REQUEST_KEY, None)
    _request_playback_refresh_window()


def _get_track_artists(track: dict) -> str:
    artists = track.get("artists") or []
    return ", ".join(
        artist.get("name")
        for artist in artists
        if isinstance(artist, dict) and artist.get("name")
    )


def _get_track_image_url(track: dict) -> str | None:
    return next(
        (
            image.get("url")
            for image in (track.get("album") or {}).get("images") or []
            if isinstance(image, dict) and image.get("url")
        ),
        None,
    )


def _get_adjacent_tracks(
    client: SpotifyClient, current_track: dict
) -> tuple[dict, dict]:
    current_track_id = current_track.get("id")
    if not current_track_id:
        return {}, {}

    cached = st.session_state.get(ADJACENT_TRACKS_CACHE_KEY)
    if cached and cached.get("current_track_id") == current_track_id:
        if (
            time.monotonic() - cached.get("fetched_at", 0)
            >= ADJACENT_TRACKS_POLL_SECONDS
        ):
            queue = client.get_queue()
            if queue is not None:
                cached["next"] = next(
                    (track for track in queue.get("queue") or [] if track.get("id")), {}
                )
                cached["fetched_at"] = time.monotonic()
        return cached.get("previous") or {}, cached.get("next") or {}

    history = st.session_state.setdefault(ADJACENT_TRACK_HISTORY_KEY, {})
    previous_request = st.session_state.pop(ADJACENT_PREVIOUS_REQUEST_KEY, None)
    if cached and current_track_id == previous_request:
        previous_track = history.get(current_track_id) or {}
    elif cached and (cached.get("current") or {}).get("id"):
        previous_track = cached["current"]
    else:
        recently_played = client.get_recently_played() or {}
        recent_tracks = [
            entry.get("track") or {} for entry in recently_played.get("items") or []
        ]
        for index, track in enumerate(recent_tracks):
            if track.get("id") and track["id"] not in history:
                history[track["id"]] = next(
                    (
                        older
                        for older in recent_tracks[index + 1 :]
                        if older.get("id") and older["id"] != track["id"]
                    ),
                    {},
                )
        previous_track = next(
            (
                track
                for track in recent_tracks
                if track.get("id") and track["id"] != current_track_id
            ),
            {},
        )

    queue = client.get_queue() or {}
    next_track = next(
        (track for track in queue.get("queue") or [] if track.get("id")), {}
    )
    history[current_track_id] = previous_track
    st.session_state[ADJACENT_TRACKS_CACHE_KEY] = {
        "current_track_id": current_track_id,
        "current": current_track,
        "previous": previous_track,
        "next": next_track,
        "fetched_at": time.monotonic(),
    }
    return previous_track, next_track


def _clear_paused_playback() -> None:
    st.session_state.pop(PAUSED_PLAYBACK_KEY, None)
    _clear_persisted_playback()


def _save_paused_playback(client: SpotifyClient, playback: dict) -> None:
    saved_playback = {**playback, "paused_at": time.monotonic()}
    queue = client.get_queue() or {}
    if (queue.get("currently_playing") or {}).get("id") == playback["item"].get("id"):
        saved_playback["queue_uris"] = [
            track["uri"]
            for track in queue.get("queue") or []
            if track.get("uri", "").startswith("spotify:track:")
        ][:99]
    st.session_state[PAUSED_PLAYBACK_KEY] = saved_playback
    _persist_playback(saved_playback)


def _resume_playback(
    client: SpotifyClient, playback: dict, live_playback: dict
) -> bool | None:
    snapshot = st.session_state.get(PAUSED_PLAYBACK_KEY) or live_playback or playback
    device_id = (snapshot.get("device") or {}).get("id")
    item = snapshot.get("item") or {}
    if not device_id:
        return None

    live_device_id = (live_playback.get("device") or {}).get("id")
    target_is_active = live_device_id == device_id
    if not target_is_active and not client.transfer_playback(device_id):
        return None

    paused_at = snapshot.get("paused_at")
    context_uri = (snapshot.get("context") or {}).get("uri")
    live_context_uri = (live_playback.get("context") or {}).get("uri")
    context_changed = bool(context_uri and live_context_uri != context_uri)
    live_track_id = (live_playback.get("item") or {}).get("id")
    missing_playback = (
        not live_playback.get("item")
        or not target_is_active
        or live_track_id != item.get("id")
        or paused_at is None
        or time.monotonic() - paused_at >= PLAYBACK_RESTORE_AFTER_SECONDS
        or context_changed
    )
    queue_uris = snapshot.get("queue_uris") or []
    context_uri = context_uri if missing_playback else None
    succeeded = client.start_playback(
        device_id=device_id,
        uri=item.get("uri") if missing_playback else None,
        position_ms=snapshot.get("progress_ms") if missing_playback else None,
        **(
            {"context_uri": context_uri}
            if context_uri and item.get("uri")
            else (
                {"uris": [item["uri"], *queue_uris]}
                if missing_playback and item.get("uri") and queue_uris
                else {}
            )
        ),
    )
    if not succeeded and context_uri and item.get("uri"):
        succeeded = client.start_playback(
            device_id=device_id,
            uri=item["uri"],
            uris=[item["uri"], *queue_uris] if queue_uris else None,
            position_ms=snapshot.get("progress_ms"),
        )
    if not succeeded and not missing_playback and item.get("uri"):
        succeeded = client.start_playback(
            device_id=device_id,
            uri=item["uri"],
            position_ms=snapshot.get("progress_ms"),
        )
    return succeeded


def _handle_play_pause(client: SpotifyClient, playback: dict) -> None:
    live_playback = client.get_current_playback() or {}
    currently_playing = bool(live_playback.get("is_playing"))
    if currently_playing:
        snapshot = live_playback
        device_id = (snapshot.get("device") or {}).get("id")
        succeeded = client.pause_playback(device_id=device_id)
        if succeeded and snapshot.get("item"):
            _save_paused_playback(client, snapshot)
    else:
        succeeded = _resume_playback(client, playback, live_playback)
        if succeeded is None:
            return

    if succeeded:
        _invalidate_playback_cache()
        _request_playback_refresh_window()
        st.rerun()
def _handle_previous_track(client: SpotifyClient, previous_track: dict) -> None:
    if client.previous_track():
        _invalidate_playback_cache()
        _clear_paused_playback()
        st.session_state[ADJACENT_PREVIOUS_REQUEST_KEY] = previous_track.get("id")
        _request_playback_refresh_window()
        st.rerun()


def _handle_next_track(client: SpotifyClient, playback: dict) -> None:
    live_playback = client.get_current_playback() or {}
    device_id = (live_playback.get("device") or playback.get("device") or {}).get("id")
    device = None
    if device_id:
        devices = client.get_devices()
        device = next(
            (
                available
                for available in (devices or {}).get("devices") or []
                if available.get("id") == device_id
            ),
            None,
        )
        if device is not None and not device.get("is_active"):
            if not client.transfer_playback(device_id):
                return

    saved_playback = st.session_state.get(PAUSED_PLAYBACK_KEY) or {}
    paused_at = saved_playback.get("paused_at")
    needs_restore = (
        bool(saved_playback)
        and not live_playback.get("is_playing")
        and (
            not live_playback.get("item")
            or (device is not None and not device.get("is_active"))
            or (
                paused_at is not None
                and time.monotonic() - paused_at >= PLAYBACK_RESTORE_AFTER_SECONDS
            )
        )
    )
    if needs_restore:
        queue = client.get_queue()
        if queue is not None:
            current_id = (live_playback.get("item") or playback.get("item") or {}).get(
                "id"
            )
            queued_current_id = (queue.get("currently_playing") or {}).get("id")
            if queued_current_id and queued_current_id != current_id:
                return
            queue_uris = [
                track["uri"]
                for track in queue.get("queue") or []
                if track.get("uri", "").startswith("spotify:track:")
            ][:100]
        else:
            queue_uris = saved_playback.get("queue_uris") or []
        current_uri = (live_playback.get("item") or playback.get("item") or {}).get(
            "uri"
        )
        if not queue_uris or queue_uris[0] == current_uri:
            return

    if needs_restore:
        context_uri = (saved_playback.get("context") or {}).get("uri")
        succeeded = (
            client.start_playback(
                device_id=device_id,
                uri=queue_uris[0],
                context_uri=context_uri,
            )
            if context_uri
            else False
        )
        if not succeeded:
            succeeded = client.start_playback(device_id=device_id, uris=queue_uris)
    else:
        succeeded = client.next_track(device_id=device_id)

    if succeeded:
        _invalidate_playback_cache()
        _clear_paused_playback()
        st.session_state.pop(ADJACENT_PREVIOUS_REQUEST_KEY, None)
        _request_playback_refresh_window()
        st.rerun()


def _is_track_liked(client: SpotifyClient, track_id: str | None) -> bool:
    if not track_id:
        return False
    pending_action = st.session_state.get("pending_library_action") or {}
    if pending_action.get("track_id") == track_id:
        return pending_action.get("action") == "like"
    liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
    if track_id not in liked_cache:
        liked_cache[track_id] = client.is_track_liked(track_id)
    return bool(liked_cache[track_id])


def _render_like_button(
    track_id: str | None, is_track_liked: bool, position: str
) -> None:
    state = "liked" if is_track_liked else "unliked"
    key = (
        "reproduction_corner_action"
        if position == "current"
        else f"reproduction_{position}_corner_action"
    )
    with st.container(key=f"reproduction-corner-action-{state}-{position}"):
        if render_button(
            "♪",
            key=key,
            style=REPRODUCTION_CORNER_ACTION_STYLE,
            help="Retirar de Liked Songs" if is_track_liked else "Añadir a Liked Songs",
            disabled=not track_id,
        ) and track_id:
            st.session_state["pending_library_action"] = {
                "track_id": track_id,
                "action": "unlike" if is_track_liked else "like",
            }
            st.rerun()


def _render_playback_controls(
    client: SpotifyClient,
    playback: dict,
    is_playing: bool,
    track_id: str | None,
    is_track_liked: bool,
    track_name: str,
    artists_text: str,
    previous_track: dict,
    next_track: dict,
) -> None:
    current_track = playback.get("item") or {}
    if track_name and not current_track.get("name"):
        current_track = {**current_track, "name": track_name}
    if artists_text and not current_track.get("artists"):
        current_track = {**current_track, "artists": [{"name": artists_text}]}
    tracks = {
        "previous": (previous_track, "Canción anterior"),
        "current": (current_track, "Sin título"),
        "next": (next_track, "Canción siguiente"),
    }

    def render_track_content(block: Block) -> None:
        _, track, part = block.key.split("-")
        if part == "title":
            _render_adjacent_track_info(*tracks[track])
            return

        label, key = {
            "previous": ("<<", "reproduction_previous"),
            "current": (">||", "reproduction_play_pause"),
            "next": (">>", "reproduction_next"),
        }[track]
        track_image = _get_track_image_url(tracks[track][0]) or _get_track_image_url(
            current_track
        )
        if render_button(
            label,
            key=key,
            style=replace(REPRODUCTION_CONTROL_STYLE, background_image=track_image),
            type="primary" if track == "current" else "secondary",
            use_container_width=True,
        ):
            if track == "previous":
                _handle_previous_track(client, previous_track)
            elif track == "current":
                _handle_play_pause(client, playback)
            else:
                _handle_next_track(client, playback)

        button_track_id = track_id if track == "current" else tracks[track][0].get("id")
        button_track_liked = (
            is_track_liked
            if track == "current"
            else _is_track_liked(client, button_track_id)
        )
        _render_like_button(button_track_id, button_track_liked, track)

    with st.container(key="reproduction-controls"):
        _render_control_styles()
        render_group(REPRODUCTION_GROUP, REPRODUCTION_TRACK_BLOCKS, render_track_content)


def _render_control_styles() -> None:
    st.html(f"""<style>
        .st-key-reproduction-controls :is(
            .st-key-lab-group-reproduction-previous,
            .st-key-lab-group-reproduction-current,
            .st-key-lab-group-reproduction-next
        ) {{
            position: relative !important;
            grid-template-rows: minmax(0, 1fr) !important;
        }}
        .st-key-reproduction-controls :is(
            .st-key-lab-group-reproduction-current,
            .st-key-lab-group-reproduction-next
        )::after {{
            content: "";
            position: absolute;
            top: 0;
            left: -2px;
            width: 4px;
            height: 100%;
            background: linear-gradient(to right, #000000 50%, #ffffff 50%);
            z-index: 6;
            pointer-events: none;
        }}
        .st-key-reproduction-controls [data-testid="stLayoutWrapper"]:has(> :is(
            .st-key-lab-reproduction-previous-title,
            .st-key-lab-reproduction-current-title,
            .st-key-lab-reproduction-next-title
        )) {{
            position: absolute !important;
            inset: 0 !important;
            height: 100% !important;
            grid-row: auto !important;
            grid-column: auto !important;
            z-index: 3;
            pointer-events: none;
        }}
        .st-key-reproduction-controls :is(
            .st-key-lab-reproduction-previous-title,
            .st-key-lab-reproduction-current-title,
            .st-key-lab-reproduction-next-title
        ) {{
            height: 100% !important;
            padding: 0 !important;
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button {{
            position: relative;
            isolation: isolate;
            overflow: hidden;
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button::before {{
            content: "";
            position: absolute;
            inset: -8px;
            z-index: -1;
            background-image: inherit;
            background-size: cover;
            background-position: center;
            filter: blur(4px);
            transform: scale(1.04);
            pointer-events: none;
        }}
        .st-key-reproduction-controls .reproduction-portrait-track-info {{
            min-height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            background: transparent !important;
        }}
        .st-key-reproduction-controls :is(
            .reproduction-portrait-track-info,
            .reproduction-track-title,
            .reproduction-track-artists,
            .reproduction-track-album
        ) {{
            color: #ffffff !important;
        }}
        .st-key-reproduction-controls :is(
            .st-key-lab-reproduction-previous-title,
            .st-key-lab-reproduction-current-title,
            .st-key-lab-reproduction-next-title
        )
        .reproduction-track-title {{
            margin-left: 48px;
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous_corner_action,
            .st-key-reproduction_corner_action,
            .st-key-reproduction_next_corner_action
        ) {{
            position: absolute !important;
            top: 0;
            right: auto;
            left: 0;
            bottom: auto;
            width: 48px !important;
            height: 48px !important;
            z-index: 4;
        }}
        @media (orientation: portrait) {{
            .st-key-reproduction-controls :is(
                .st-key-lab-group-reproduction-current,
                .st-key-lab-group-reproduction-next
            )::after {{
                top: -2px;
                left: 0;
                width: 100%;
                height: 4px;
                background: linear-gradient(to bottom, #000000 50%, #ffffff 50%);
            }}
            .st-key-reproduction-controls .reproduction-portrait-track-info {{
                padding: 0 !important;
            }}
        }}
        </style>""")


def _render_adjacent_track_info(track: dict, fallback_title: str) -> None:
    title = html.escape(track.get("name") or fallback_title)
    artists = html.escape(_get_track_artists(track) or "Artista desconocido")
    album = html.escape((track.get("album") or {}).get("name") or "Álbum desconocido")
    st.markdown(
        f'<div class="reproduction-portrait-track-info reproduction-adjacent-info">'
        f'<div class="reproduction-track-title">{title}</div>'
        f'<div class="reproduction-track-details">'
        f'<button type="button" class="reproduction-detail-button" '
        f'aria-label="Artista" title="Artista">'
        f'<span class="reproduction-detail-icon" aria-hidden="true">person</span></button>'
        f'<div class="reproduction-track-metadata">'
        f'<div class="reproduction-track-artists" title="{artists}">{artists}</div>'
        f'<div class="reproduction-track-album" title="{album}">{album}</div>'
        f'</div>'
        f'<button type="button" class="reproduction-detail-button" '
        f'aria-label="Álbum" title="Álbum">'
        f'<span class="reproduction-detail-icon" aria-hidden="true">album</span></button>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every=PLAYBACK_FRAGMENT_INTERVAL_SECONDS)
def render_reproduction_page():
    render_page_style(REPRODUCTION_PAGE_STYLE)
    st.markdown(
        """
        <style>
        .st-key-reproduction-controls {
            margin-top: 0 !important;
        }

        .reproduction-track-title {
            color: #ffffff !important;
            font-size: min(5cqi, 1.4rem);
            font-weight: 800;
            line-height: 24px;
            box-sizing: border-box;
            padding: 12px 0.5rem;
            white-space: nowrap;
            text-overflow: ellipsis;
            overflow: hidden;
            background: rgba(0, 0, 0, 0.5);
            opacity: 1 !important;
            text-shadow: none !important;
        }
        .reproduction-track-details {
            display: grid;
            grid-row: 3;
            grid-template-columns: 48px minmax(0, 1fr) 48px;
            min-width: 0;
            align-items: stretch;
            background: rgba(0, 0, 0, 0.5);
        }
        .reproduction-track-metadata {
            display: grid;
            grid-template-rows: repeat(2, minmax(0, 1fr));
            min-width: 0;
            padding: 0 0.5rem;
        }
        .st-key-reproduction-controls .reproduction-detail-button {
            box-sizing: border-box;
            width: 48px;
            height: 48px;
            padding: 0;
            border: none;
            border-radius: 0;
            display: grid;
            place-items: center;
            background: transparent;
            color: #ffffff !important;
            font-size: 1.5rem;
            line-height: 1;
            cursor: pointer;
            pointer-events: auto;
        }
        .st-key-reproduction-controls .reproduction-detail-icon {
            font-family: "Material Symbols Rounded" !important;
            font-size: 28px;
            font-weight: 400;
            font-style: normal;
            font-feature-settings: "liga";
            font-variation-settings: "FILL" 1, "wght" 400, "GRAD" 0, "opsz" 24;
            line-height: 1;
            letter-spacing: 0;
            color: inherit !important;
        }
        .st-key-reproduction-controls .reproduction-detail-button:focus-visible {
            outline: 2px solid #ffffff;
            outline-offset: -2px;
        }
        .reproduction-track-artists,
        .reproduction-track-album {
            color: #ffffff !important;
            font-size: min(22cqh, 4cqi, 1rem);
            line-height: 1.1;
            align-self: center;
            min-width: 0;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            opacity: 1 !important;
            text-shadow: none !important;
        }
        .reproduction-track-artists {
            text-align: left;
        }
        .reproduction-track-album {
            text-align: right;
        }
        .reproduction-portrait-track-info {
            box-sizing: border-box;
            display: grid;
            grid-template-rows: 48px minmax(0, 1fr) 48px;
            position: absolute;
            inset: 0;
            text-align: center;
            width: 100%;
            min-height: 0;
            margin: 0;
            padding: 0 0.5rem;
            overflow-wrap: anywhere;
            overflow: hidden;
            container-type: size;
            color: #ffffff !important;
            background: rgba(0, 0, 0, 0.5) !important;
        }
        .reproduction-adjacent-info {
            min-height: 0;
            margin: 0;
        }
        .st-key-reproduction-controls :is(
            .st-key-lab-reproduction-previous-title,
            .st-key-lab-reproduction-current-title,
            .st-key-lab-reproduction-next-title
        ) > [data-testid="stElementContainer"] {
            position: absolute;
            inset: 0;
            height: auto !important;
            min-height: 0 !important;
        }
        .st-key-reproduction-controls :is(
            .st-key-lab-reproduction-previous-title,
            .st-key-lab-reproduction-current-title,
            .st-key-lab-reproduction-next-title
        ) :is(.stMarkdown, [data-testid="stMarkdownContainer"]) {
            height: 100% !important;
            min-height: 0 !important;
        }

        .st-key-reproduction-controls [data-testid="stColumn"],
        .st-key-reproduction-controls [data-testid="column"],
        .st-key-reproduction-controls .stColumn {
            padding: 0 !important;
            min-height: 0;
        }
        .st-key-reproduction-controls [data-testid="stVerticalBlock"]:has(> .st-key-reproduction_play_pause) {
            position: relative !important;
        }
        .st-key-reproduction-cover-art {
            position: relative !important;
        }
        .st-key-reproduction-controls [data-testid="stHorizontalBlock"] > [data-testid="stVerticalBlock"] {
            display: flex;
            justify-content: center;
        }
        .st-key-reproduction-controls [data-testid="stElementContainer"] {
            width: 100%;
            margin: 0 !important;
            padding: 0 !important;
            container-type: inline-size;
        }
        .st-key-reproduction-controls [data-testid="stButton"] button {
            line-height: 1;
            white-space: nowrap;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.9);
            background-color: #ffffff;
            background-repeat: no-repeat;
            background-position: center;
        }
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button p {
            display: inline-flex;
            width: 1.8em;
            height: 1.8em;
            margin: 0;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            background: rgba(0, 0, 0, 0.5);
            color: #ffffff !important;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.95);
        }
        .st-key-reproduction_play_pause [data-testid="stButton"] {
            display: flex;
            width: 100% !important;
            justify-content: center;
        }
        .st-key-reproduction_play_pause [data-testid="stButton"] > div,
        .st-key-reproduction_play_pause [data-testid="stTooltipIcon"],
        .st-key-reproduction_play_pause [data-testid="stTooltipHoverTarget"] {
            width: 100% !important;
        }
        .st-key-reproduction_play_pause [data-testid="stTooltipHoverTarget"] {
            justify-content: center !important;
        }
        .st-key-reproduction-controls [data-testid="stButton"] button * {
            font-size: inherit !important;
            line-height: 1 !important;
            white-space: nowrap;
            font-weight: 900 !important;
        }
        .st-key-reproduction_previous [data-testid="stButton"] button,
        .st-key-reproduction_next [data-testid="stButton"] button {
            width: 100% !important;
            background-image: none;
            background-repeat: no-repeat;
        }
        .st-key-reproduction_previous [data-testid="stButton"],
        .st-key-reproduction_next [data-testid="stButton"] {
            display: flex;
            width: 100% !important;
            justify-content: center;
        }
        .st-key-reproduction_previous [data-testid="stButton"] > div,
        .st-key-reproduction_next [data-testid="stButton"] > div,
        .st-key-reproduction_previous [data-testid="stTooltipIcon"],
        .st-key-reproduction_next [data-testid="stTooltipIcon"],
        .st-key-reproduction_previous [data-testid="stTooltipHoverTarget"],
        .st-key-reproduction_next [data-testid="stTooltipHoverTarget"] {
            width: 100% !important;
        }
        .st-key-reproduction_previous [data-testid="stTooltipHoverTarget"],
        .st-key-reproduction_next [data-testid="stTooltipHoverTarget"] {
            justify-content: center !important;
        }
        @media (orientation: landscape) {
            .st-key-reproduction-controls .reproduction-portrait-track-info {
                min-height: 0 !important;
                margin: 0 !important;
                padding: 0 0.5rem !important;
                position: absolute !important;
                inset: 0;
                z-index: 3;
                color: #ffffff !important;
                background: rgba(0, 0, 0, 0.5) !important;
            }
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button {
            opacity: 1 !important;
            border: none !important;
            text-shadow: none !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button * {
            color: inherit !important;
            text-shadow: none !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-liked-"]
        :is(
            .st-key-reproduction_previous_corner_action,
            .st-key-reproduction_corner_action,
            .st-key-reproduction_next_corner_action
        )
        [data-testid="stButton"] button {
            color: #000000 !important;
            background: rgba(0, 0, 0, 0.5) !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-liked-"]
        :is(
            .st-key-reproduction_previous_corner_action,
            .st-key-reproduction_corner_action,
            .st-key-reproduction_next_corner_action
        )
        [data-testid="stButton"] button p {
            display: grid;
            place-items: center;
            width: 30px;
            height: 30px;
            margin: 0;
            border-radius: 50%;
            background: #ffffff !important;
            color: #000000 !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-unliked-"]
        [data-testid="stButton"] button {
            color: #ffffff !important;
            background: rgba(0, 0, 0, 0.5) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    # st.subheader("Reproduction")
    if not has_spotify_auth_available():
        render_spotify_authorization_section()
        return

    try:
        client = _get_spotify_client()
    except Exception:
        return

    reproduction_service = ReproductionService(client)
    playback = _get_playback(client)
    is_playing = bool(playback.get("is_playing"))

    item = playback.get("item") or {}
    track_id = item.get("id")

    is_track_liked = _is_track_liked(client, track_id)
    track_name = item.get("name")
    artists = item.get("artists") or []
    artist_names = [artist.get("name") for artist in artists if artist.get("name")]
    album_images = (item.get("album") or {}).get("images") or []
    track_image_url = next(
        (
            image.get("url")
            for image in album_images
            if isinstance(image, dict) and image.get("url")
        ),
        None,
    )
    previous_track, next_track = _get_adjacent_tracks(client, item)

    artists_text = ", ".join(artist_names)

    _render_playback_controls(
        client=client,
        playback=playback,
        is_playing=is_playing,
        track_id=track_id,
        is_track_liked=is_track_liked,
        track_name=track_name or "",
        artists_text=artists_text,
        previous_track=previous_track,
        next_track=next_track,
    )

    pending_library_action = st.session_state.pop("pending_library_action", None)
    if pending_library_action:
        pending_track_id = pending_library_action["track_id"]
        action = pending_library_action["action"]
        action_succeeded = (
            reproduction_service.save_liked_track(pending_track_id)
            if action == "like"
            else reproduction_service.remove_liked_track(pending_track_id)
        )
        liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
        if action_succeeded:
            liked_cache[pending_track_id] = action == "like"
        result_key = f"{'liked' if action == 'like' else 'unliked'}_track_{'saved' if action_succeeded else 'error'}"
        st.session_state[result_key] = True
        st.rerun()
