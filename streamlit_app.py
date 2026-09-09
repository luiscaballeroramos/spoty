import json
import os
import threading
from datetime import timedelta, timezone

import pandas as pd
import streamlit as st
from psycopg import connect
from psycopg.rows import dict_row


def load_streamlit_secrets_to_env():
    try:
        secrets = st.secrets
    except Exception:
        return

    database_url = None
    try:
        database_url = secrets["DATABASE_URL"]
    except Exception:
        database_url = None

    if not database_url:
        try:
            database_url = secrets["connections"]["neon"]["url"]
        except Exception:
            database_url = None

    if database_url:
        os.environ["DATABASE_URL"] = str(database_url).strip()


load_streamlit_secrets_to_env()

from config import DATABASE_URL

st.set_page_config(
    page_title="SPOTY",
    page_icon="🎵",
    layout="wide",
)

register_listeningevents = None
registration_running = False


def _run_registration_task():
    global registration_running

    try:
        register_function = get_register_listeningevents()
        if register_function is None:
            raise RuntimeError("No se pudo importar la función de registro del repo.")

        register_function()
    finally:
        registration_running = False


def start_registration():
    global registration_running

    if registration_running:
        return

    registration_running = True

    thread = threading.Thread(target=_run_registration_task, daemon=True)
    thread.start()


def get_register_listeningevents():
    global register_listeningevents

    if register_listeningevents is not None:
        return register_listeningevents

    try:
        from _registration import register_listeningevents as imported_function
    except Exception:
        return None

    register_listeningevents = imported_function
    return register_listeningevents


LOCAL_TIMEZONE = timezone(timedelta(hours=4))


def query_database(query: str):
    with connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall()


def format_duration(duration_ms):
    if pd.isna(duration_ms):
        return "-"

    total_seconds = int(duration_ms / 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def get_image(images):
    if not images:
        return None

    try:
        parsed = json.loads(images)
        if isinstance(parsed, list) and parsed:
            return parsed[0]
    except (json.JSONDecodeError, TypeError):
        pass

    return None


@st.fragment(run_every="10s")
def spotify_dashboard():
    register_function = get_register_listeningevents()

    if register_function is None:
        st.caption(
            "Registro disponible solo si el repo está configurado correctamente."
        )
    else:
        st.button(
            "↻ Actualizar",
            key="register_listeningevents",
            disabled=registration_running,
            on_click=start_registration,
        )

    rows = query_database("""
        WITH track_stats AS (
            SELECT
                track_id,
                COUNT(*) AS track_play_count,
                CEIL(
                    100.0
                    * ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC, track_id)
                    / NULLIF(COUNT(*) OVER (), 0)
                )::int AS track_top_percent
            FROM listening_events
            GROUP BY track_id
        )
        SELECT
            le.played_at,
            le.track_id,
            ts.track_play_count,
            ts.track_top_percent,
            t.name AS track_name,
            t.duration_ms,
            t.artists_ids,
            a.name AS album_name,
            a.images AS album_images,
            a.release_year
        FROM listening_events AS le
        LEFT JOIN track_stats AS ts ON le.track_id = ts.track_id
        LEFT JOIN tracks AS t ON le.track_id = t.id
        LEFT JOIN albums AS a ON t.album_id = a.id
        ORDER BY le.played_at DESC
        LIMIT 100
        """)

    df = pd.DataFrame(rows)

    summary = query_database("""
        SELECT
            (SELECT COUNT(*) FROM tracks) AS total_tracks,
            (SELECT COUNT(*) FROM artists) AS total_artists,
            (SELECT COUNT(*) FROM albums) AS total_albums,
            (SELECT COUNT(*) FROM liked_tracks) AS total_liked_tracks,
            (SELECT COUNT(*) FROM listening_events) AS total_listening_events
        """)

    if summary:
        summary = pd.Series(summary[0])
    else:
        summary = pd.Series(
            {
                "total_tracks": 0,
                "total_artists": 0,
                "total_albums": 0,
                "total_liked_tracks": 0,
                "total_listening_events": 0,
            }
        )

    if df.empty:
        st.info("No hay listening events registrados todavía.")
        return

    artist_ids = set()
    for value in df["artists_ids"].dropna():
        try:
            ids = json.loads(value)
            if isinstance(ids, list):
                artist_ids.update(ids)
        except (json.JSONDecodeError, TypeError):
            continue

    artist_names = {}
    if artist_ids:
        artist_array = ",".join(
            f"'{artist_id.replace(chr(39), chr(39) * 2)}'" for artist_id in artist_ids
        )
        artist_rows = query_database(f"""
            SELECT id, name
            FROM artists
            WHERE id = ANY(ARRAY[{artist_array}]::text[])
            """)
        artist_names = {row["id"]: row["name"] for row in artist_rows}

    def get_artists(value):
        if not value:
            return "-"

        try:
            ids = json.loads(value)
            if not isinstance(ids, list):
                return "-"
            return ", ".join(
                artist_names.get(artist_id, artist_id) for artist_id in ids
            )
        except (json.JSONDecodeError, TypeError):
            return "-"

    df["artist"] = df["artists_ids"].apply(get_artists)
    df["played_at"] = pd.to_datetime(df["played_at"], unit="s", utc=True).dt.tz_convert(
        LOCAL_TIMEZONE
    )
    df["played_at"] = df["played_at"].dt.strftime("%d/%m/%Y %H:%M")
    df["duration"] = df["duration_ms"].apply(format_duration)
    df["album_image"] = df["album_images"].apply(get_image)

    last = df.iloc[0]

    col1, col2 = st.columns([1, 4])
    with col1:
        if last["album_image"]:
            st.image(last["album_image"], width="stretch")

    with col2:
        st.markdown(f"### {last['track_name']}")
        st.write(f"👤 **{last['artist']}**")
        st.write(f"💿 **{last['album_name']}**")
        st.write(
            f"🎧 **x{int(last['track_play_count'])} ({int(last['track_top_percent']) if pd.notna(last['track_top_percent']) else '-'}%)**"
        )
        st.write(f"🕐 **{last['played_at']}**")

    st.divider()

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("🎵 Tracks", int(summary["total_tracks"]))
    with col2:
        st.metric("👤 Artistas", int(summary["total_artists"]))
    with col3:
        st.metric("💿 Álbumes", int(summary["total_albums"]))
    with col4:
        st.metric("💜 Liked Songs", int(summary["total_liked_tracks"]))
    with col5:
        st.metric("🎧 Listening Events", int(summary["total_listening_events"]))

    st.divider()
    st.subheader("🎧 Historial")

    display_df = df[["played_at", "track_name", "artist", "album_name"]].rename(
        columns={
            "played_at": "🕐 Reproducido",
            "track_name": "🎵 Canción",
            "artist": "👤 Artista",
            "album_name": "💿 Álbum",
        }
    )

    st.dataframe(display_df, width="stretch", hide_index=True)


spotify_dashboard()
