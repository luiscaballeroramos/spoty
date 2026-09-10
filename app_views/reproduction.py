import streamlit as st

from _reproduction import _get_current_playback, _next_track, _pause, _play, _previous_track
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_oauth_feedback,
    render_spotify_authorization_section,
)


def _render_playback_controls(is_playing: bool) -> None:
    prev_col, play_pause_col, next_col = st.columns(3, gap="small")

    play_pause_label = "⏸️" if is_playing else "▶️"
    play_pause_help = "Pausar" if is_playing else "Reproducir"
    play_pause_action = _pause if is_playing else _play

    with prev_col:
        if st.button(
            "⏮️",
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
            "⏭️",
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
        div[data-testid="stHorizontalBlock"]:has(button[kind="primary"][data-testid="stBaseButton-primary"]) button,
        div[data-testid="stHorizontalBlock"]:has(button[kind="secondary"][data-testid="stBaseButton-secondary"]) button {
            min-height: 4rem;
            font-size: 1.8rem;
            border-radius: 0.8rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("Reproduction")
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

    if track_name:
        st.caption(
            f"Ahora: {track_name}"
            + (f" - {', '.join(artist_names)}" if artist_names else "")
        )
    else:
        st.caption("No hay una pista activa en este momento.")

    _render_playback_controls(is_playing=is_playing)
