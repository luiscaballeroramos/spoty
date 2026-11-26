from datetime import datetime, timezone

import streamlit as st


def render_csv_listening_history(query_service) -> None:
    """Render recent consolidated listening events without live playback controls."""
    st.title("Historial de escuchas")
    st.caption("Escuchas consolidadas de la revisión CSV seleccionada; vista de solo lectura.")
    limit = st.selectbox("Escuchas recientes", (25, 50, 100, 250), index=1)
    events = query_service.recent_listening_events(limit)
    if not events:
        st.info("No hay escuchas consolidadas en esta revisión.")
        return

    artist_ids = sorted({
        artist_id
        for event in events
        for artist_id in (
            event.get("artists_ids") if isinstance(event.get("artists_ids"), list) else []
        )
        if isinstance(artist_id, str)
    })
    artist_names = query_service.artist_names(artist_ids)
    rows = []
    for event in events:
        artists = event.get("artists_ids")
        if not isinstance(artists, list):
            artists = []
        played_at = datetime.fromtimestamp(event["played_at"], timezone.utc)
        rows.append({
            "Fecha (UTC)": played_at.strftime("%Y-%m-%d %H:%M:%S"),
            "Canción": event.get("track_name") or event["track_id"],
            "Artistas": ", ".join(artist_names.get(artist_id, artist_id) for artist_id in artists),
            "Álbum": event.get("album_name") or "",
            "Año": event.get("release_year"),
            "Reproducciones": event["track_play_count"],
            "Top (%)": event["track_top_percent"],
            "ID Spotify": event["track_id"],
        })

    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption(f"Mostrando {len(rows)} escuchas consolidadas.")
