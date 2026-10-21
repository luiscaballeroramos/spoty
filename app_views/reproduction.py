import html
import time
from dataclasses import replace
from pathlib import Path

import streamlit as st

from streamlit_lab import (
    Block,
    BlockStyle,
    ButtonStyle,
    Group,
    Insets,
    Layout,
    PageStyle,
    Size,
)
from streamlit_lab.renderer import render_button, render_group, render_page_style
from app_views.error_feedback import clear_incident, render_incident_notice
from services.errors import LikedTrackReplicaPendingError
from services.playback import PlaybackController, PlaybackSnapshotStore
from services.reproduction import ReproductionService
from spotifyapi.spotifyclient import SpotifyClient
from spotifyapi.errors import SpotifyError, SpotifyRequestError
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_spotify_authorization_section,
)

PLAYBACK_PLAYING_POLL_SECONDS = 5
PLAYBACK_PAUSED_POLL_SECONDS = 5
PLAYBACK_FRAGMENT_INTERVAL_SECONDS = 1
PLAYBACK_RESTORE_AFTER_SECONDS = 60
ADJACENT_TRACKS_POLL_SECONDS = 30
PLAYBACK_CACHE_KEY = "reproduction_playback_cache"
PAUSED_PLAYBACK_KEY = "reproduction_paused_playback"
PERSISTED_PLAYBACK_LOADED_KEY = "reproduction_persisted_playback_loaded"
PLAYBACK_STATE_PATH = Path(__file__).resolve().parents[1] / ".playback_state.json"
SPOTIFY_CLIENT_CACHE_KEY = "reproduction_spotify_client"
SPOTIFY_CLIENT_MAX_AGE_SECONDS = 50 * 60
LIKED_CACHE_KEY = "reproduction_liked_cache"
REPRODUCTION_VIEW_SIGNATURE_KEY = "reproduction_view_signature"
LAST_VALID_VIEW_STATE_KEY = "reproduction_last_valid_view_state"
REPRODUCTION_ERROR_KEY = "reproduction_spotify_error"
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
    border="1px solid rgba(255, 255, 255, 0.8)",
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
    border="1px solid rgba(255, 255, 255, 0.8)",
    border_radius="8px",
    background="rgba(0, 0, 0, 0.5)",
    padding="0",
    width="56px",
    height="48px",
    min_height="48px",
    font_size="1.5rem",
    text_align="center",
    box_shadow="0 4px 10px rgba(0, 0, 0, 0.55), inset 0 1px 1px rgba(255, 255, 255, 0.22), inset 0 -3px 0 rgba(0, 0, 0, 0.4)",
)


def _playback_controller(client: SpotifyClient) -> PlaybackController:
    return PlaybackController(
        client=client,
        state=st.session_state,
        snapshot_store=PlaybackSnapshotStore(PLAYBACK_STATE_PATH),
        monotonic=time.monotonic,
        playing_poll_seconds=PLAYBACK_PLAYING_POLL_SECONDS,
        paused_poll_seconds=PLAYBACK_PAUSED_POLL_SECONDS,
        restore_after_seconds=PLAYBACK_RESTORE_AFTER_SECONDS,
        playback_cache_key=PLAYBACK_CACHE_KEY,
        paused_playback_key=PAUSED_PLAYBACK_KEY,
        persisted_loaded_key=PERSISTED_PLAYBACK_LOADED_KEY,
        previous_request_key=ADJACENT_PREVIOUS_REQUEST_KEY,
        adjacent_cache_key=ADJACENT_TRACKS_CACHE_KEY,
        adjacent_history_key=ADJACENT_TRACK_HISTORY_KEY,
        adjacent_poll_seconds=ADJACENT_TRACKS_POLL_SECONDS,
    )


def _persist_playback(playback: dict) -> None:
    """Persist playback through a temporary file, converting pause time to a restart-safe epoch timestamp."""
    PlaybackSnapshotStore(PLAYBACK_STATE_PATH).save(playback)


def _get_spotify_client() -> SpotifyClient:
    """Reuse the session's Spotify client until its maximum age requires creating a new one."""
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
    """Copy playback and advance valid playing progress without exceeding the track duration."""
    return PlaybackController.estimate_progress(playback, fetched_at, now)


def _get_playback(client: SpotifyClient) -> dict:
    """Read cached or live playback, persist valid tracks, and use the saved pause when no item is reported."""
    return _playback_controller(client).get_playback()


def _invalidate_playback_cache() -> None:
    """Remove cached playback so the next read queries Spotify."""
    st.session_state.pop(PLAYBACK_CACHE_KEY, None)


def refresh_playback_on_page_entry() -> None:
    """Clear playback and adjacent-track caches and history when entering the reproduction page."""
    _invalidate_playback_cache()
    st.session_state.pop(ADJACENT_TRACKS_CACHE_KEY, None)
    st.session_state.pop(ADJACENT_TRACK_HISTORY_KEY, None)
    st.session_state.pop(ADJACENT_PREVIOUS_REQUEST_KEY, None)


def _get_track_artists(track: dict) -> str:
    """Join available artist names into a comma-separated display string."""
    artists = track.get("artists") or []
    return ", ".join(
        artist.get("name")
        for artist in artists
        if isinstance(artist, dict) and artist.get("name")
    )


def _get_track_image_url(track: dict) -> str | None:
    """Return the first available album image URL, or None when no image exists."""
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
    """Resolve the previous track from session history or recent plays and the next track from the cached queue."""
    return _playback_controller(client).adjacent_tracks(current_track)


def _clear_paused_playback() -> None:
    """Remove the paused playback from session state and delete its persisted snapshot."""
    st.session_state.pop(PAUSED_PLAYBACK_KEY, None)
    PlaybackSnapshotStore(PLAYBACK_STATE_PATH).clear()


def _save_paused_playback(client: SpotifyClient, playback: dict) -> None:
    """Save pause time and matching queue URIs in session state and persist the playback snapshot."""
    _playback_controller(client).save_paused_playback(playback)


def _start_playback_for_restore(client: SpotifyClient, **kwargs) -> bool:
    """Start restoration, returning False only for explicit 400/404 rejections and propagating other errors."""
    return _playback_controller(client).start_playback_for_restore(**kwargs)


def _resume_playback(
    client: SpotifyClient, playback: dict, live_playback: dict
) -> bool | None:
    """Resume on the saved device, restoring track, context, queue, and position when live state is insufficient."""
    return _playback_controller(client).resume_playback(playback, live_playback)


def _handle_play_pause(client: SpotifyClient, playback: dict) -> None:
    """Choose pause or resume from live state, save successful pauses, and rerun after a successful action."""
    succeeded = _playback_controller(client).toggle_playback(playback)
    if succeeded:
        clear_incident(st.session_state, "playback-command")
        st.rerun()


def _handle_previous_track(client: SpotifyClient, previous_track: dict) -> None:
    """Skip backward, clear outdated playback, record the requested previous track, and rerun on success."""
    if _playback_controller(client).previous_track(previous_track):
        clear_incident(st.session_state, "playback-command")
        st.rerun()


def _handle_next_track(client: SpotifyClient, playback: dict) -> None:
    """Skip forward or restore the remaining queue after a long pause, clearing saved playback on success."""
    if _playback_controller(client).next_track(playback):
        clear_incident(st.session_state, "playback-command")
        st.rerun()


def _cache_liked_statuses(
    client: SpotifyClient, track_ids: list[str | None]
) -> None:
    """Fetch uncached valid track IDs in one batch and store their liked status only after a successful read."""
    liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
    uncached_track_ids = list(
        dict.fromkeys(
            track_id
            for track_id in track_ids
            if track_id and track_id not in liked_cache
        )
    )
    if uncached_track_ids:
        liked_cache.update(client.are_tracks_liked(uncached_track_ids))


def _get_reproduction_view_state(client: SpotifyClient) -> dict:
    """Assemble playback, adjacent tracks, artist text, and liked status for the three visible cards."""
    playback = _get_playback(client)
    item = playback.get("item") or {}
    track_id = item.get("id")
    previous_track, next_track = _get_adjacent_tracks(client, item)
    _cache_liked_statuses(
        client,
        [track_id, previous_track.get("id"), next_track.get("id")],
    )
    return {
        "playback": playback,
        "item": item,
        "track_id": track_id,
        "previous_track": previous_track,
        "next_track": next_track,
        "is_track_liked": _is_track_liked(client, track_id),
        "is_previous_track_liked": _is_track_liked(
            client, previous_track.get("id")
        ),
        "is_next_track_liked": _is_track_liked(client, next_track.get("id")),
        "artists_text": _get_track_artists(item),
    }


def _read_reproduction_view_state(client: SpotifyClient) -> dict:
    """Store successful view reads or return the last valid view as stale, raising if none is available."""
    try:
        view_state = _get_reproduction_view_state(client)
    except SpotifyError as exc:
        st.session_state[REPRODUCTION_ERROR_KEY] = exc
        saved_state = st.session_state.get(LAST_VALID_VIEW_STATE_KEY)
        if saved_state is None:
            raise
        return {**saved_state, "is_stale": True}
    except Exception as exc:
        error = RuntimeError("Playback data could not be processed.")
        error.action = "process playback view"
        error.__cause__ = exc
        st.session_state[REPRODUCTION_ERROR_KEY] = error
        saved_state = st.session_state.get(LAST_VALID_VIEW_STATE_KEY)
        if saved_state is None:
            raise error from exc
        return {**saved_state, "is_stale": True}
    st.session_state.pop(REPRODUCTION_ERROR_KEY, None)
    clear_incident(st.session_state, "playback-poll")
    st.session_state[LAST_VALID_VIEW_STATE_KEY] = view_state
    return {**view_state, "is_stale": False}


def _track_view_signature(track: dict) -> tuple:
    """Extract track identity and displayed metadata for detecting visible card changes."""
    album = track.get("album") or {}
    return (
        track.get("id"),
        track.get("name"),
        _get_track_artists(track),
        album.get("name"),
        _get_track_image_url(track),
    )


def _reproduction_view_signature(view_state: dict) -> tuple:
    """Build a signature of playback mode, visible tracks, and liked status while excluding progress."""
    playback = view_state["playback"]
    return (
        bool(playback.get("is_playing")),
        _track_view_signature(view_state["item"]),
        _track_view_signature(view_state["previous_track"]),
        _track_view_signature(view_state["next_track"]),
        view_state["is_track_liked"],
        view_state["is_previous_track_liked"],
        view_state["is_next_track_liked"],
    )


def _is_track_liked(client: SpotifyClient, track_id: str | None) -> bool:
    """Resolve liked status from a pending action or cache, fetching it when needed and rejecting failed reads."""
    if not track_id:
        return False
    pending_action = st.session_state.get("pending_library_action") or {}
    if pending_action.get("track_id") == track_id:
        return pending_action.get("action") == "like"
    liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
    if track_id not in liked_cache:
        _cache_liked_statuses(client, [track_id])
    return bool(liked_cache[track_id])


def _render_like_button(
    track_id: str | None, is_track_liked: bool, position: str
) -> None:
    """Render a card's liked toggle and queue its action before rerunning, disabling tracks without an ID."""
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
    """Render the three track cards with playback commands, artwork, metadata, and liked toggles."""
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
        """Render a card's metadata or artwork controls and dispatch clicks to the matching track action."""
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
    """Inject CSS for card overlays, separators, blurred adjacent artwork, and corner-action placement."""
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
        .st-key-reproduction-controls [data-testid="stButton"] button {{
            border-radius: 0 !important;
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button {{
            box-shadow: inset 0 2px 0 rgba(255, 255, 255, 0.18),
                inset 0 -4px 0 rgba(0, 0, 0, 0.28);
            transition: transform 120ms ease, filter 120ms ease, box-shadow 120ms ease;
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button:hover:not(:disabled) {{
            filter: brightness(1.06);
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button:active:not(:disabled) {{
            transform: translateY(2px) scale(0.99);
            filter: brightness(0.82);
            box-shadow: inset 0 5px 12px rgba(0, 0, 0, 0.55);
        }}
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button:focus-visible {{
            outline: 2px solid #ffffff;
            outline-offset: -4px;
        }}
        @media (prefers-reduced-motion: reduce) {{
            .st-key-reproduction-controls :is(
                .st-key-reproduction_previous,
                .st-key-reproduction_play_pause,
                .st-key-reproduction_next
            ) [data-testid="stButton"] button {{
                transition: none;
            }}
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
            margin-left: 56px;
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
            width: 56px !important;
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
    """Render escaped title, artist, and album metadata with fallback labels and accessible detail buttons."""
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


@st.fragment
def _render_reproduction_controls():
    """Render the reproduction fragment, surface Spotify errors, and execute pending library actions."""
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
            grid-template-columns: 56px minmax(0, 1fr) 56px;
            grid-template-rows: repeat(2, minmax(0, 1fr));
            min-width: 0;
            align-items: stretch;
            background: rgba(0, 0, 0, 0.5);
        }
        .reproduction-track-metadata {
            display: grid;
            grid-column: 2;
            grid-row: 1 / span 2;
            grid-template-rows: repeat(2, minmax(0, 1fr));
            min-width: 0;
            padding: 0 0.5rem;
        }
        .st-key-reproduction-controls .reproduction-detail-button {
            box-sizing: border-box;
            width: 100%;
            height: 100%;
            min-height: 0;
            padding: 0;
            border: 1px solid rgba(255, 255, 255, 0.8);
            border-radius: 8px;
            display: grid;
            place-items: center;
            background: linear-gradient(155deg, rgba(48, 48, 48, 0.96), rgba(0, 0, 0, 0.88));
            color: #ffffff !important;
            font-size: 1.5rem;
            line-height: 1;
            cursor: pointer;
            pointer-events: auto;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.55),
                inset 0 1px 1px rgba(255, 255, 255, 0.25),
                inset 0 -3px 0 rgba(0, 0, 0, 0.45);
            transition: transform 100ms ease, filter 100ms ease, box-shadow 100ms ease;
        }
        .st-key-reproduction-controls .reproduction-detail-button:first-child {
            grid-column: 1;
            grid-row: 1 / span 2;
        }
        .st-key-reproduction-controls .reproduction-detail-button:last-child {
            grid-column: 3;
            grid-row: 1 / span 2;
        }
        .st-key-reproduction-controls .reproduction-detail-button:hover {
            filter: brightness(1.25);
        }
        .st-key-reproduction-controls .reproduction-detail-button:active {
            transform: translateY(3px) scale(0.92);
            filter: brightness(0.72);
            box-shadow: inset 0 4px 8px rgba(0, 0, 0, 0.75),
                0 1px 2px rgba(0, 0, 0, 0.35);
        }
        .st-key-reproduction-controls .reproduction-detail-icon {
            font-family: "Material Symbols Rounded" !important;
            font-size: 22px;
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
            outline-offset: 2px;
        }
        @media (prefers-reduced-motion: reduce) {
            .st-key-reproduction-controls .reproduction-detail-button {
                transition: none;
            }
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
            border: 1px solid rgba(255, 255, 255, 0.65);
            background: linear-gradient(155deg, rgba(48, 48, 48, 0.96), rgba(0, 0, 0, 0.88));
            color: #ffffff !important;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.95);
            box-shadow: 0 5px 12px rgba(0, 0, 0, 0.6),
                inset 0 1px 1px rgba(255, 255, 255, 0.3),
                inset 0 -3px 0 rgba(0, 0, 0, 0.5);
            transition: transform 100ms ease, filter 100ms ease, box-shadow 100ms ease;
        }
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button:hover:not(:disabled) p {
            filter: brightness(1.25);
        }
        .st-key-reproduction-controls :is(
            .st-key-reproduction_previous,
            .st-key-reproduction_play_pause,
            .st-key-reproduction_next
        ) [data-testid="stButton"] button:active:not(:disabled) p {
            transform: translateY(3px) scale(0.92);
            filter: brightness(0.72);
            box-shadow: inset 0 4px 8px rgba(0, 0, 0, 0.75),
                0 1px 2px rgba(0, 0, 0, 0.35);
        }
        @media (prefers-reduced-motion: reduce) {
            .st-key-reproduction-controls :is(
                .st-key-reproduction_previous,
                .st-key-reproduction_play_pause,
                .st-key-reproduction_next
            ) [data-testid="stButton"] button p {
                transition: none;
            }
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
            border-radius: 8px !important;
            text-shadow: none !important;
            transition: transform 100ms ease, filter 100ms ease, box-shadow 100ms ease;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button:hover:not(:disabled) {
            filter: brightness(1.2);
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button:active:not(:disabled) {
            transform: translateY(2px) scale(0.96);
            filter: brightness(0.75);
            box-shadow: inset 0 4px 8px rgba(0, 0, 0, 0.7),
                0 1px 2px rgba(0, 0, 0, 0.35) !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button:focus-visible {
            outline: 2px solid #ffffff;
            outline-offset: 2px;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button * {
            color: inherit !important;
            text-shadow: none !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
        [data-testid="stButton"] button p {
            display: grid;
            place-items: center;
            width: 100%;
            height: 100%;
            margin: 0;
            border: 0;
            border-radius: inherit;
            background: transparent !important;
            color: #ffffff !important;
            font-size: 1.5rem;
            box-shadow: none;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-liked-"]
        :is(
            .st-key-reproduction_previous_corner_action,
            .st-key-reproduction_corner_action,
            .st-key-reproduction_next_corner_action
        )
        [data-testid="stButton"] button {
            color: #000000 !important;
            background: #ffffff !important;
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-liked-"]
        :is(
            .st-key-reproduction_previous_corner_action,
            .st-key-reproduction_corner_action,
            .st-key-reproduction_next_corner_action
        )
        [data-testid="stButton"] button p {
            background: transparent !important;
            color: #000000 !important;
            box-shadow: none !important;
        }
        @media (prefers-reduced-motion: reduce) {
            .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
            [data-testid="stButton"] button,
            .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-"]
            [data-testid="stButton"] button p {
                transition: none;
            }
        }
        .st-key-reproduction-controls [class*="st-key-reproduction-corner-action-unliked-"]
        [data-testid="stButton"] button {
            color: #ffffff !important;
            background: linear-gradient(155deg, rgba(48, 48, 48, 0.96), rgba(0, 0, 0, 0.88)) !important;
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
    except SpotifyError as exc:
        render_incident_notice(
            "playback-client", exc, "No se pudo consultar la reproducción de Spotify."
        )
        return
    except Exception as exc:
        render_incident_notice(
            "playback-client", exc, "No se pudo inicializar el cliente de reproducción."
        )
        return
    clear_incident(st.session_state, "playback-client")

    reproduction_service = ReproductionService(client)
    try:
        view_state = _read_reproduction_view_state(client)
    except SpotifyError as exc:
        render_incident_notice(
            "playback-poll", exc, "No se pudo leer la reproducción de Spotify."
        )
        return
    except Exception as exc:
        render_incident_notice(
            "playback-poll", exc, "No se pudo procesar el estado de reproducción."
        )
        return
    if view_state["is_stale"]:
        error = st.session_state.get(REPRODUCTION_ERROR_KEY) or RuntimeError(
            "Playback data is stale."
        )
        render_incident_notice(
            "playback-poll",
            error,
            "No se pudo actualizar Spotify. Se muestra el ultimo estado valido.",
        )
    playback = view_state["playback"]
    is_playing = bool(playback.get("is_playing"))
    item = view_state["item"]
    track_id = view_state["track_id"]
    track_name = item.get("name")
    previous_track = view_state["previous_track"]
    next_track = view_state["next_track"]
    is_track_liked = view_state["is_track_liked"]
    artists_text = view_state["artists_text"]
    st.session_state[REPRODUCTION_VIEW_SIGNATURE_KEY] = (
        _reproduction_view_signature(view_state)
    )

    try:
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
    except SpotifyError as exc:
        render_incident_notice(
            "playback-command", exc, "Spotify no pudo completar el control de reproducción."
        )
        return
    clear_incident(st.session_state, "playback-command")

    pending_library_action = st.session_state.pop("pending_library_action", None)
    if pending_library_action:
        pending_track_id = pending_library_action["track_id"]
        action = pending_library_action["action"]
        try:
            action_succeeded = (
                reproduction_service.save_liked_track(pending_track_id)
                if action == "like"
                else reproduction_service.remove_liked_track(pending_track_id)
            )
        except SpotifyError as exc:
            render_incident_notice(
                "liked-track-remote", exc, "Spotify no pudo actualizar este favorito."
            )
            return
        except LikedTrackReplicaPendingError as exc:
            render_incident_notice(
                "liked-track-replica",
                exc,
                "Spotify cambió el favorito, pero la réplica local quedó pendiente.",
            )
            return
        liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
        if action_succeeded:
            liked_cache[pending_track_id] = action == "like"
            clear_incident(st.session_state, "liked-track-remote")
            clear_incident(st.session_state, "liked-track-replica")
        result_key = f"{'liked' if action == 'like' else 'unliked'}_track_{'saved' if action_succeeded else 'error'}"
        st.session_state[result_key] = True
        st.rerun()


def _check_reproduction_view_state() -> None:
    """Poll the view and request a rerun when visible state or the presence of a Spotify error changes."""
    if not has_spotify_auth_available():
        return

    had_error = REPRODUCTION_ERROR_KEY in st.session_state
    try:
        client = _get_spotify_client()
        view_state = _read_reproduction_view_state(client)
    except SpotifyError as exc:
        st.session_state[REPRODUCTION_ERROR_KEY] = exc
        view_state = None
    except Exception as exc:
        st.session_state[REPRODUCTION_ERROR_KEY] = RuntimeError(
            "Playback refresh failed."
        )
        st.session_state[REPRODUCTION_ERROR_KEY].action = "refresh playback"
        return

    has_error = REPRODUCTION_ERROR_KEY in st.session_state
    if had_error != has_error:
        st.rerun()
    if view_state is None or view_state["is_stale"]:
        return
    signature = _reproduction_view_signature(view_state)
    previous_signature = st.session_state.get(REPRODUCTION_VIEW_SIGNATURE_KEY)
    if previous_signature is None:
        st.session_state[REPRODUCTION_VIEW_SIGNATURE_KEY] = signature
    elif signature != previous_signature:
        st.rerun()


@st.fragment(run_every=PLAYBACK_FRAGMENT_INTERVAL_SECONDS)
def _watch_reproduction_view_state() -> None:
    """Run the periodic fragment that checks for playback changes without rebuilding unchanged cards."""
    _check_reproduction_view_state()


def render_reproduction_page() -> None:
    """Render reproduction controls and start the periodic view-state watcher."""
    _render_reproduction_controls()
    _watch_reproduction_view_state()
