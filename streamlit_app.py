import json
import os
from datetime import timedelta, timezone
from urllib.parse import parse_qs, urlparse

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

from config import DATABASE_URL, REDIRECT_URI

st.set_page_config(
    page_title="SPOTY",
    page_icon="🎵",
    layout="wide",
)

register_listeningevents = None


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


def _get_query_param_value(name: str):
    try:
        value = st.query_params.get(name)
    except Exception:
        params = st.experimental_get_query_params()
        value = params.get(name)

    if isinstance(value, list):
        return value[0] if value else None

    return value


def _clear_oauth_query_params() -> None:
    try:
        query_params = st.query_params
        for key in ("code", "state", "error"):
            if key in query_params:
                del query_params[key]
        return
    except Exception:
        pass


def _extract_oauth_code_and_state(raw_callback: str):
    value = (raw_callback or "").strip()
    if not value:
        return None, None

    if value.startswith("http://") or value.startswith("https://"):
        query_text = urlparse(value).query
    elif "?" in value:
        query_text = value.split("?", 1)[1]
    else:
        query_text = value

    parsed = parse_qs(query_text)
    oauth_code = parsed.get("code", [None])[0]
    oauth_state = parsed.get("state", [None])[0]

    if oauth_code:
        return oauth_code, oauth_state

    # Allow pasting only the authorization code.
    return value, None


def complete_spotify_oauth_authorization(oauth_code: str, oauth_state: str = None):
    expected_state = st.session_state.get("spotify_oauth_state")
    if expected_state and oauth_state and oauth_state != expected_state:
        st.session_state["oauth_feedback"] = {
            "type": "error",
            "message": "El estado OAuth no coincide. Pulsa Autorizar Spotify de nuevo.",
        }
        st.session_state.pop("spotify_oauth_state", None)
        return

    try:
        from spotifyapi.spotifyclient import get_oauth_manager

        oauth_manager = get_oauth_manager()
        token_info = oauth_manager.get_access_token(
            code=oauth_code,
            as_dict=True,
            check_cache=False,
        )
        if not token_info:
            raise RuntimeError("Spotify no devolvió un token válido.")
    except Exception as exc:
        st.session_state["oauth_feedback"] = {
            "type": "error",
            "message": f"No se pudo completar la autorización OAuth: {exc}",
        }
    else:
        st.session_state["oauth_feedback"] = {
            "type": "success",
            "message": "Spotify autorizado correctamente. Ya puedes pulsar Actualizar.",
        }
        st.session_state["spotify_oauth_manual_callback"] = ""
    finally:
        st.session_state.pop("spotify_oauth_state", None)

    try:
        current = st.experimental_get_query_params()
        current.pop("code", None)
        current.pop("state", None)
        current.pop("error", None)
        st.experimental_set_query_params(**current)
    except Exception:
        pass


def process_spotify_oauth_callback() -> None:
    oauth_error = _get_query_param_value("error")
    oauth_code = _get_query_param_value("code")
    oauth_state = _get_query_param_value("state")

    if not oauth_error and not oauth_code:
        return

    if oauth_error:
        st.session_state["oauth_feedback"] = {
            "type": "error",
            "message": f"Spotify devolvió un error de autorización: {oauth_error}",
        }
        st.session_state.pop("spotify_oauth_state", None)
        _clear_oauth_query_params()
        return

    complete_spotify_oauth_authorization(oauth_code, oauth_state)
    _clear_oauth_query_params()


def get_spotify_authorize_url():
    try:
        from spotifyapi.spotifyclient import get_oauth_manager

        oauth_manager = get_oauth_manager()
        state = st.session_state.get("spotify_oauth_state")
        if not state:
            state = os.urandom(16).hex()
            st.session_state["spotify_oauth_state"] = state
        return oauth_manager.get_authorize_url(state=state), None
    except Exception as exc:
        return None, str(exc)


def render_oauth_feedback():
    feedback = st.session_state.get("oauth_feedback")
    if not feedback:
        return

    message = feedback.get("message")
    feedback_type = feedback.get("type")

    if not message:
        return

    if feedback_type == "error":
        st.error(message)
    elif feedback_type == "warning":
        st.warning(message)
    else:
        st.success(message)


def render_spotify_authorization_section() -> None:
    authorize_url, auth_error = get_spotify_authorize_url()

    if auth_error:
        st.error(f"No se pudo iniciar OAuth de Spotify: {auth_error}")
        return

    st.markdown(f"[🔐 Autorizar Spotify]({authorize_url})")
    st.caption(
        "Tras autorizar, Spotify te redirige a esta app y se guardará el token en .cache. "
        f"Redirect URI configurado: {REDIRECT_URI}"
    )

    st.caption(
        "Si aparece ERR_CONNECTION_REFUSED en 127.0.0.1, copia la URL final del navegador "
        "(la que contiene ?code=...) y pégala aquí para completar OAuth."
    )

    manual_callback = st.text_input(
        "Callback OAuth (URL completa o solo code)",
        key="spotify_oauth_manual_callback",
        placeholder="http://127.0.0.1:8888/?code=...&state=...",
    )

    if st.button("Completar autorización", key="complete_spotify_oauth"):
        oauth_code, oauth_state = _extract_oauth_code_and_state(manual_callback)
        if not oauth_code:
            st.session_state["oauth_feedback"] = {
                "type": "error",
                "message": "No se encontró el parámetro code en el callback pegado.",
            }
        else:
            complete_spotify_oauth_authorization(oauth_code, oauth_state)


def has_spotify_auth_available() -> bool:
    if os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip():
        return True

    try:
        from spotifyapi.spotifyclient import has_cached_oauth_token
    except Exception:
        return False

    return has_cached_oauth_token()


def run_registration_with_feedback():
    register_function = get_register_listeningevents()

    if register_function is None:
        st.session_state["registration_feedback"] = {
            "type": "error",
            "message": "No se pudo importar la función de registro del repo.",
        }
        return

    try:
        with st.spinner("Actualizando datos desde Spotify..."):
            summary = register_function() or {}
    except Exception as exc:
        st.session_state["registration_feedback"] = {
            "type": "error",
            "message": f"Actualización fallida: {exc}",
        }
        return

    fetched_items = summary.get("fetched_items")
    inserted_events = summary.get("inserted_listening_events")

    if isinstance(fetched_items, int) and isinstance(inserted_events, int):
        st.session_state["registration_feedback"] = {
            "type": "success",
            "message": (
                "Actualización completada. "
                f"Elementos leídos: {fetched_items}. "
                f"Listening events nuevos: {inserted_events}."
            ),
        }
    else:
        st.session_state["registration_feedback"] = {
            "type": "success",
            "message": "Actualización completada.",
        }


def render_registration_feedback():
    feedback = st.session_state.get("registration_feedback")
    if not feedback:
        return

    message = feedback.get("message")
    feedback_type = feedback.get("type")

    if not message:
        return

    if feedback_type == "error":
        st.error(message)
    elif feedback_type == "warning":
        st.warning(message)
    else:
        st.success(message)


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
    auth_available = has_spotify_auth_available()

    render_oauth_feedback()
    render_registration_feedback()

    if register_function is None:
        st.caption(
            "Registro disponible solo si el repo está configurado correctamente."
        )
    else:
        if not auth_available:
            st.warning(
                "No hay sesión OAuth válida de Spotify. Autoriza desde aquí para continuar."
            )
            render_spotify_authorization_section()

        if st.button(
            "↻ Actualizar",
            key="register_listeningevents",
            disabled=not auth_available,
        ):
            run_registration_with_feedback()

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


process_spotify_oauth_callback()
spotify_dashboard()
