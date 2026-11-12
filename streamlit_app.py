from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from spoty_data_reader import (
    DATA_BRANCH,
    DATA_REPOSITORY_NAME,
    DATA_REPOSITORY_OWNER,
    DataReadError,
    GitHubDataReader,
    SpotySnapshot,
)


@st.cache_data(ttl=300)
def load_snapshot(token: str | None) -> SpotySnapshot:
    return GitHubDataReader(token=token).load()


def _track_name(track_id: str, tracks: dict[str, dict]) -> str:
    track = tracks.get(track_id)
    return track.get("name") or track_id if track else track_id


def render_dashboard(snapshot: SpotySnapshot) -> None:
    tracks = {row["id"]: row for row in snapshot.tracks}
    artists = {row["id"]: row.get("name") for row in snapshot.artists}
    plays = Counter(row["track_id"] for row in snapshot.listening_events)
    latest = max(snapshot.listening_events, key=lambda row: row["played_at"], default=None)
    latest_text = (
        datetime.fromtimestamp(latest["played_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if latest else "Sin escuchas"
    )

    st.title("spoty · Resumen de escuchas")
    st.caption(f"Datos de spoty-data · commit `{snapshot.commit[:7]}`")
    columns = st.columns(4)
    columns[0].metric("Escuchas", f"{len(snapshot.listening_events):,}")
    columns[1].metric("Canciones escuchadas", f"{len(plays):,}")
    columns[2].metric("Canciones en catálogo", f"{len(snapshot.tracks):,}")
    columns[3].metric("Artistas en catálogo", f"{len(snapshot.artists):,}")
    st.caption(f"Ultima escucha registrada: {latest_text}")

    top_tracks = plays.most_common(10)
    if top_tracks:
        chart = pd.DataFrame(
            [
                {"Cancion": _track_name(track_id, tracks), "Escuchas": count}
                for track_id, count in top_tracks
            ]
        ).set_index("Cancion")
        st.subheader("Canciones mas escuchadas")
        st.bar_chart(chart)

    recent = sorted(
        snapshot.listening_events,
        key=lambda row: row["played_at"],
        reverse=True,
    )[:10]
    if recent:
        rows = []
        for event in recent:
            track = tracks.get(event["track_id"], {})
            artist_ids = track.get("artists_ids") or []
            rows.append({
                "Fecha (UTC)": datetime.fromtimestamp(
                    event["played_at"], timezone.utc,
                ).strftime("%Y-%m-%d %H:%M"),
                "Cancion": track.get("name") or event["track_id"],
                "Artista": ", ".join(
                    artists.get(artist_id) or artist_id for artist_id in artist_ids
                ),
            })
        st.subheader("Escuchas recientes")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="spoty · Resumen", page_icon="🎵", layout="wide")
    token = os.environ.get("SPOTY_DATA_READ_TOKEN", "").strip() or None
    if token is None:
        try:
            token = st.secrets.get("SPOTY_DATA_READ_TOKEN", "").strip() or None
        except StreamlitSecretNotFoundError:
            token = None
    if st.button("Actualizar datos"):
        load_snapshot.clear()
    try:
        snapshot = load_snapshot(token)
    except (DataReadError, ValueError) as error:
        st.title("spoty · Resumen de escuchas")
        st.error(str(error))
        st.caption(
            f"Fuente: {DATA_REPOSITORY_OWNER}/{DATA_REPOSITORY_NAME}, "
            f"rama {DATA_BRANCH}. Si es privado, configura SPOTY_DATA_READ_TOKEN "
            "con permiso de solo lectura."
        )
        return
    render_dashboard(snapshot)


if __name__ == "__main__":
    main()
