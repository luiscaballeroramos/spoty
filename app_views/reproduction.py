import json

import streamlit as st

from _reproduction import _get_current_playback, _next_track, _pause, _play, _previous_track
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_oauth_feedback,
    render_spotify_authorization_section,
)


def _render_playback_controls(is_playing: bool) -> None:
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

    _render_playback_controls(is_playing=is_playing)
