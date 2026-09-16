import json

import streamlit as st

from _reproduction import (
    _get_current_playback,
    _is_track_liked,
    _like_track,
    _next_track,
    _pause,
    _play,
    _previous_track,
    _unlike_track,
)
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_oauth_feedback,
    render_spotify_authorization_section,
)


PLAYBACK_REFRESH_MARGIN_MS = 2_000


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


def _render_playback_controls(
    is_playing: bool, track_id: str | None, is_track_liked: bool
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
                    st.rerun()
                st.error("No se pudo volver a la canción anterior.")

        with play_pause_col:
            with st.container(key="reproduction-cover"):
                if st.button(
                    play_pause_label,
                    key="reproduction_play_pause",
                    type="primary",
                    help=play_pause_help,
                    use_container_width=True,
                ):
                    if play_pause_action():
                        st.rerun()
                    action_label = "pausar" if is_playing else "reanudar"
                    st.error(f"No se pudo {action_label} la reproducción.")

                if st.button(
                    "♥" if is_track_liked else "♡",
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

        with next_col:
            if st.button(
                ">>",
                key="reproduction_next",
                help="Siguiente",
                use_container_width=True,
            ):
                if _next_track():
                    st.rerun()
                st.error("No se pudo avanzar a la siguiente canción.")


@st.fragment(run_every=1)
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
            background-size: cover;
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
            height: auto !important;
            aspect-ratio: 1;
            background-image:
                linear-gradient(rgba(0, 0, 0, 0.28), rgba(0, 0, 0, 0.5)),
                url("https://images.unsplash.com/photo-1493225457124-a3eb161ffa5f?auto=format&fit=crop&w=900&q=85");
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
        .st-key-reproduction_corner_action {
            position: absolute !important;
            left: 0.75rem;
            bottom: 0.75rem;
            width: 3rem !important;
            z-index: 2;
        }
        .st-key-reproduction_corner_action [data-testid="stButton"] button {
            width: 3rem !important;
            height: 3rem !important;
            min-height: 3rem !important;
            aspect-ratio: 1;
            padding: 0 !important;
            border: 1px solid rgba(255, 255, 255, 0.7);
            border-radius: 50%;
            background: rgba(0, 0, 0, 0.72) !important;
            color: white !important;
            font-size: 1.6rem !important;
            opacity: 1 !important;
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

    playback = _get_current_playback() or {}
    is_playing = bool(playback.get("is_playing"))

    item = playback.get("item") or {}
    track_id = item.get("id")
    if _has_reached_track_refresh_window(playback):
        refresh_key = (track_id, item.get("duration_ms"))
        if st.session_state.get("reproduction_refresh_key") != refresh_key:
            st.session_state["reproduction_refresh_key"] = refresh_key
            st.rerun(scope="fragment")

    pending_library_action = st.session_state.get("pending_library_action") or {}
    is_current_track_pending = bool(
        track_id and pending_library_action.get("track_id") == track_id
    )
    if is_current_track_pending:
        is_track_liked = pending_library_action.get("action") == "like"
    else:
        is_track_liked = bool(track_id and _is_track_liked(track_id))
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
        st.caption(
            f"Ahora: {track_name}"
            + (f" - {', '.join(artist_names)}" if artist_names else "")
        )
    else:
        st.caption("No hay una pista activa en este momento.")

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
        result_key = f"{'liked' if action == 'like' else 'unliked'}_track_{'saved' if action_succeeded else 'error'}"
        st.session_state[result_key] = True
        st.rerun()
