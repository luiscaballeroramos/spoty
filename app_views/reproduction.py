import html
import json
import time

import streamlit as st

from _reproduction import (
    _get_current_playback,
    _is_track_liked,
    _like_track,
    _next_track,
    _pause,
    _play,
    _previous_track,
    _get_queue,
    _get_recently_played,
    _unlike_track,
)
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_oauth_feedback,
    render_spotify_authorization_section,
)


PLAYBACK_REFRESH_MARGIN_MS = 2_000
PLAYBACK_FRAGMENT_INTERVAL_SECONDS = 1
PLAYBACK_PAUSED_POLL_SECONDS = 5
PLAYBACK_CONTROL_REFRESH_SECONDS = 4
PLAYBACK_CACHE_KEY = "reproduction_playback_cache"
PLAYBACK_FORCE_REFRESH_UNTIL_KEY = "reproduction_force_refresh_until"
LIKED_CACHE_KEY = "reproduction_liked_cache"
ADJACENT_TRACKS_CACHE_KEY = "reproduction_adjacent_tracks_cache"


def _has_reached_track_refresh_window(playback: dict) -> bool:
    if not playback.get("is_playing"):
        return False

    item = playback.get("item") or {}
    progress_ms = playback.get("progress_ms")
    duration_ms = item.get("duration_ms")
    if not isinstance(progress_ms, (int, float)) or not isinstance(
        duration_ms, (int, float)
    ):
        return False

    return progress_ms + PLAYBACK_REFRESH_MARGIN_MS >= duration_ms


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


def _get_playback() -> dict:
    now = time.monotonic()
    force_refresh_until = st.session_state.get(PLAYBACK_FORCE_REFRESH_UNTIL_KEY, 0)
    should_force_refresh = now < force_refresh_until
    if force_refresh_until and not should_force_refresh:
        st.session_state.pop(PLAYBACK_FORCE_REFRESH_UNTIL_KEY, None)

    cached = st.session_state.get(PLAYBACK_CACHE_KEY)
    if cached and not should_force_refresh:
        playback = cached["playback"]
        item = playback.get("item") or {}
        progress_ms = playback.get("progress_ms")
        duration_ms = item.get("duration_ms")
        elapsed_ms = (now - cached["fetched_at"]) * 1000

        if not playback.get("is_playing"):
            if elapsed_ms < PLAYBACK_PAUSED_POLL_SECONDS * 1000:
                return playback
        elif (
            isinstance(progress_ms, (int, float))
            and isinstance(duration_ms, (int, float))
            and progress_ms + elapsed_ms + PLAYBACK_REFRESH_MARGIN_MS < duration_ms
        ):
            return _copy_playback_with_estimated_progress(
                playback, cached["fetched_at"], now
            )
        elif not isinstance(progress_ms, (int, float)) or not isinstance(
            duration_ms, (int, float)
        ):
            return playback

    playback = _get_current_playback() or {}
    st.session_state[PLAYBACK_CACHE_KEY] = {
        "playback": playback,
        "fetched_at": now,
    }
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


def _get_adjacent_tracks(current_track_id: str | None) -> tuple[dict, dict]:
    cached = st.session_state.get(ADJACENT_TRACKS_CACHE_KEY)
    if cached and cached.get("current_track_id") == current_track_id:
        return cached.get("previous") or {}, cached.get("next") or {}

    previous_track = {}
    recently_played = _get_recently_played() or {}
    for entry in recently_played.get("items") or []:
        track = entry.get("track") or {}
        if track.get("id") and track.get("id") != current_track_id:
            previous_track = track
            break

    queue = _get_queue() or {}
    next_track = next(
        (track for track in queue.get("queue") or [] if track.get("id")), {}
    )
    st.session_state[ADJACENT_TRACKS_CACHE_KEY] = {
        "current_track_id": current_track_id,
        "previous": previous_track,
        "next": next_track,
    }
    return previous_track, next_track


def _render_playback_controls(
    is_playing: bool,
    track_id: str | None,
    is_track_liked: bool,
    track_name: str,
    artists_text: str,
    previous_track: dict,
    next_track: dict,
) -> None:
    play_pause_label = ">||"
    play_pause_help = "Pausar" if is_playing else "Reproducir"
    play_pause_action = _pause if is_playing else _play

    with st.container(key="reproduction-controls"):
        previous_col, play_pause_col, next_col = st.columns(
            3, gap=None, vertical_alignment="center"
        )

        with previous_col:
            if st.button(
                "<<",
                key="reproduction_previous",
                help="Anterior",
                use_container_width=True,
            ):
                if _previous_track():
                    _invalidate_playback_cache()
                    st.session_state.pop(ADJACENT_TRACKS_CACHE_KEY, None)
                    _request_playback_refresh_window()
                    st.rerun()
                st.error("No se pudo volver a la canción anterior.")
            _render_adjacent_track_info(previous_track, "Canción anterior")

        with play_pause_col:
            with st.container(key="reproduction-cover"):
                with st.container(key="reproduction-cover-art"):
                    if st.button(
                        play_pause_label,
                        key="reproduction_play_pause",
                        type="primary",
                        help=play_pause_help,
                        use_container_width=True,
                    ):
                        if play_pause_action():
                            _invalidate_playback_cache()
                            _request_playback_refresh_window()
                            st.rerun()
                        action_label = "pausar" if is_playing else "reanudar"
                        st.error(f"No se pudo {action_label} la reproducción.")

                    with st.container(
                        key=(
                            "reproduction-corner-action-liked"
                            if is_track_liked
                            else "reproduction-corner-action-unliked"
                        )
                    ):
                        if st.button(
                            "♥",
                            key="reproduction_corner_action",
                            help=(
                                "Retirar de Liked Songs"
                                if is_track_liked
                                else "Añadir a Liked Songs"
                            ),
                            disabled=not track_id,
                        ):
                            if track_id:
                                st.session_state["pending_library_action"] = {
                                    "track_id": track_id,
                                    "action": "unlike" if is_track_liked else "like",
                                }
                                st.rerun()

                st.markdown(
                    f"""
                    <div class="reproduction-portrait-track-info">
                        <div class="reproduction-track-title">{html.escape(track_name or 'Sin título')}</div>
                        <div class="reproduction-track-artists">{html.escape(artists_text or 'Artista desconocido')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        with next_col:
            if st.button(
                ">>",
                key="reproduction_next",
                help="Siguiente",
                use_container_width=True,
            ):
                if _next_track():
                    _invalidate_playback_cache()
                    st.session_state.pop(ADJACENT_TRACKS_CACHE_KEY, None)
                    _request_playback_refresh_window()
                    st.rerun()
                st.error("No se pudo avanzar a la siguiente canción.")
            _render_adjacent_track_info(next_track, "Canción siguiente")


def _render_adjacent_track_info(track: dict, fallback_title: str) -> None:
    title = html.escape(track.get("name") or fallback_title)
    artists = html.escape(_get_track_artists(track) or "Artista desconocido")
    st.markdown(
        f'<div class="reproduction-portrait-track-info reproduction-adjacent-info">'
        f'<div class="reproduction-track-title">{title}</div>'
        f'<div class="reproduction-track-artists">{artists}</div></div>',
        unsafe_allow_html=True,
    )


@st.fragment(run_every=PLAYBACK_FRAGMENT_INTERVAL_SECONDS)
def render_reproduction_page():
    st.markdown(
        """
        <style>
        [data-testid="stHeader"] {
            display: none !important;
        }

        [data-testid="stMainBlockContainer"] {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            overflow: hidden !important;
        }

        [data-testid="stAppViewContainer"] {
            overflow-y: hidden !important;
        }

        [data-testid="stMainBlockContainer"] > div,
        [data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {
            gap: 0 !important;
        }

        [data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] {
            row-gap: 0 !important;
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }

        [data-testid="stMainBlockContainer"] [data-testid="stElementContainer"] {
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }

        .st-key-reproduction-controls {
            margin-top: 0 !important;
        }

        [data-testid="stCaptionContainer"] {
            margin-top: 0 !important;
            margin-bottom: 0.1rem !important;
        }

        .reproduction-track-info {
            text-align: center;
            margin: 0.35rem 0 1.25rem;
            padding: 0 1rem;
            opacity: 1 !important;
            display: none;
        }
        .reproduction-track-title {
            color: white !important;
            font-size: clamp(1.15rem, 3vw, 1.8rem);
            font-weight: 800;
            line-height: 1.2;
            overflow-wrap: anywhere;
            opacity: 1 !important;
            text-shadow: none !important;
        }
        .reproduction-track-artists,
        .reproduction-track-empty {
            color: white !important;
            font-size: clamp(0.95rem, 2vw, 1.15rem);
            line-height: 1.3;
            font-weight: 600;
            opacity: 1 !important;
            text-shadow: none !important;
        }
        .reproduction-portrait-track-info {
            display: block;
            text-align: center;
            min-height: 3.25rem;
            margin: 0 0 0.35rem;
            padding: 0 0.5rem;
            overflow-wrap: anywhere;
        }
        .reproduction-adjacent-info {
            min-height: 3.25rem;
            margin: 0 0 0.35rem;
        }

        .st-key-reproduction-controls [data-testid="stHorizontalBlock"] {
            gap: 0 !important;
        }
        .st-key-reproduction-controls [data-testid="column"] {
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
            height: min(calc(100vh - 8.5rem), 32vw) !important;
            justify-content: center;
        }
        .st-key-reproduction-controls [data-testid="stElementContainer"] {
            width: 100%;
            margin: 0 !important;
            padding: 0 !important;
            container-type: inline-size;
        }
        .st-key-reproduction-controls [data-testid="stButton"] button {
            width: 100% !important;
            height: min(calc(100vh - 8.5rem), 32vw) !important;
            aspect-ratio: auto;
            min-height: 0;
            box-sizing: border-box;
            font-size: clamp(1.75rem, 8vw, 5rem) !important;
            line-height: 1;
            white-space: nowrap;
            padding: 0 0.15rem;
            border-radius: 0.8rem;
            color: white;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.9);
            background-color: #34495e;
            background-size: contain;
            background-repeat: no-repeat;
            background-position: center;
        }
        .st-key-reproduction-controls [data-testid="stButton"] button * {
            font-size: inherit !important;
            line-height: 1 !important;
            white-space: nowrap;
            font-weight: 900 !important;
        }
        .st-key-reproduction_previous [data-testid="stButton"] button,
        .st-key-reproduction_next [data-testid="stButton"] button {
            width: 75% !important;
            height: min(calc(100vh - 13rem), 27vw) !important;
            aspect-ratio: 1;
            background-image: linear-gradient(rgba(0, 0, 0, 0.28), rgba(0, 0, 0, 0.5));
            background-size: contain;
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
                min-height: 4.25rem !important;
                margin-bottom: 0.75rem;
                padding-top: 0.35rem;
                position: relative;
                z-index: 3;
                background: #0d1017;
            }
            .st-key-reproduction-cover .reproduction-portrait-track-info {
                min-height: 4.25rem !important;
            }
            .st-key-reproduction_previous [data-testid="stButton"] button,
            .st-key-reproduction_next [data-testid="stButton"] button {
                height: min(calc(100vh - 14rem), 26vw) !important;
            }
        }
        @media (orientation: portrait) {
            .st-key-reproduction-controls {
                --reproduction-portrait-button-size: min(75vw, calc((100vh - 12rem) / 3));
            }
            .st-key-reproduction-controls [data-testid="stHorizontalBlock"] {
                display: grid !important;
                grid-template-columns: minmax(0, 1fr) !important;
                align-items: stretch !important;
            }
            .st-key-reproduction-controls .stColumn {
                width: 100% !important;
                min-width: 0 !important;
            }
            .st-key-reproduction-controls .stColumn > [data-testid="stVerticalBlock"] {
                width: 100% !important;
            }
            .st-key-reproduction-controls [data-testid="stHorizontalBlock"] > [data-testid="stVerticalBlock"] {
                width: 100% !important;
                flex: none !important;
                height: auto !important;
            }
            .st-key-reproduction-controls [data-testid="stButton"] button {
                width: var(--reproduction-portrait-button-size) !important;
                height: var(--reproduction-portrait-button-size) !important;
                aspect-ratio: 1 !important;
            }
            .st-key-reproduction_play_pause [data-testid="stButton"] {
                display: flex;
                justify-content: center;
            }
            .st-key-reproduction_previous [data-testid="stButton"] > div:last-child,
            .st-key-reproduction_play_pause [data-testid="stButton"] > div:last-child,
            .st-key-reproduction_next [data-testid="stButton"] > div:last-child {
                display: flex;
                justify-content: center;
                width: 100% !important;
            }
            .st-key-reproduction_play_pause [data-testid="stButton"] button {
                width: var(--reproduction-portrait-button-size) !important;
                aspect-ratio: 1 !important;
            }
            .st-key-reproduction_previous [data-testid="stButton"] button,
            .st-key-reproduction_next [data-testid="stButton"] button {
                width: var(--reproduction-portrait-button-size) !important;
                height: var(--reproduction-portrait-button-size) !important;
                aspect-ratio: 1 !important;
            }
        }
        .st-key-reproduction_corner_action {
            position: absolute !important;
            left: 0.75rem;
            bottom: 0.75rem;
            width: 4rem !important;
            z-index: 2;
        }
        .st-key-reproduction_corner_action [data-testid="stButton"] button {
            width: 4rem !important;
            height: 4rem !important;
            min-height: 4rem !important;
            aspect-ratio: 1;
            padding: 0 !important;
            border: 1px solid rgba(255, 255, 255, 0.7);
            border-radius: 50%;
            background: rgba(0, 0, 0, 0.28) !important;
            color: white !important;
            font-size: 1.6rem !important;
            opacity: 1 !important;
        }
        .st-key-reproduction-corner-action-liked
        .st-key-reproduction_corner_action [data-testid="stButton"] button {
            color: white !important;
        }
        .st-key-reproduction-corner-action-unliked
        .st-key-reproduction_corner_action [data-testid="stButton"] button {
            color: #444444 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # st.subheader("Reproduction")
    render_oauth_feedback()

    if not has_spotify_auth_available():
        st.warning(
            "No hay sesión OAuth válida de Spotify. Autoriza desde aquí para usar los controles."
        )
        render_spotify_authorization_section()
        return

    playback = _get_playback()
    is_playing = bool(playback.get("is_playing"))

    item = playback.get("item") or {}
    track_id = item.get("id")

    pending_library_action = st.session_state.get("pending_library_action") or {}
    is_current_track_pending = bool(
        track_id and pending_library_action.get("track_id") == track_id
    )
    if is_current_track_pending:
        is_track_liked = pending_library_action.get("action") == "like"
    else:
        liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
        if track_id not in liked_cache:
            liked_cache[track_id] = _is_track_liked(track_id)
        is_track_liked = bool(track_id and liked_cache[track_id])
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
    previous_track, next_track = _get_adjacent_tracks(track_id)

    adjacent_images_css = ""
    previous_image_url = _get_track_image_url(previous_track)
    next_image_url = _get_track_image_url(next_track)
    if previous_image_url:
        adjacent_images_css += (
            f'.st-key-reproduction_previous [data-testid="stButton"] button '
            f'{{ background-image: linear-gradient(rgba(0, 0, 0, 0.28), '
            f'rgba(0, 0, 0, 0.5)), url({json.dumps(previous_image_url)}); }}'
        )
    if next_image_url:
        adjacent_images_css += (
            f'.st-key-reproduction_next [data-testid="stButton"] button '
            f'{{ background-image: linear-gradient(rgba(0, 0, 0, 0.28), '
            f'rgba(0, 0, 0, 0.5)), url({json.dumps(next_image_url)}); }}'
        )
    if adjacent_images_css:
        st.markdown(f"<style>{adjacent_images_css}</style>", unsafe_allow_html=True)

    if track_image_url:
        st.markdown(
            f"""
            <style>
            .st-key-reproduction_play_pause [data-testid="stButton"] button {{
                background-image:
                    linear-gradient(rgba(0, 0, 0, 0.28), rgba(0, 0, 0, 0.5)),
                    url({json.dumps(track_image_url)});
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    if track_name:
        artists_text = ", ".join(artist_names)
        st.markdown(
            f"""
            <div class="reproduction-track-info">
                <div class="reproduction-track-title">{track_name}</div>
                {f'<div class="reproduction-track-artists">{artists_text}</div>' if artists_text else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        artists_text = ""
        st.markdown(
            '<div class="reproduction-track-info reproduction-track-empty">'
            "No hay una pista activa en este momento."
            "</div>",
            unsafe_allow_html=True,
        )

    if st.session_state.pop("liked_track_saved", False):
        st.toast("Canción añadida a Liked Songs y a la base de datos.")
    if st.session_state.pop("liked_track_error", False):
        st.error("No se pudo guardar la canción en Liked Songs.")
    if st.session_state.pop("unliked_track_saved", False):
        st.toast("Canción retirada de Liked Songs y de la base de datos.")
    if st.session_state.pop("unliked_track_error", False):
        st.error("No se pudo retirar la canción de Liked Songs.")

    _render_playback_controls(
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
            _like_track(pending_track_id)
            if action == "like"
            else _unlike_track(pending_track_id)
        )
        liked_cache = st.session_state.setdefault(LIKED_CACHE_KEY, {})
        if action_succeeded:
            liked_cache[pending_track_id] = action == "like"
        result_key = f"{'liked' if action == 'like' else 'unliked'}_track_{'saved' if action_succeeded else 'error'}"
        st.session_state[result_key] = True
        st.rerun()
