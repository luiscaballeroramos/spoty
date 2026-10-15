import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import DISPLAY_TIMEZONE
from app_views.error_feedback import (
    clear_incident,
    record_incident,
    render_incident_notice,
)
from services.summary_queries import SummaryQueryService
from services.synchronization import SyncStatus
from spotifyapi.errors import SpotifyError
from spotifyapi.streamlit_auth import (
    has_spotify_auth_available,
    render_spotify_authorization_section,
)

LOCAL_TIMEZONE = ZoneInfo(DISPLAY_TIMEZONE)
SIGNATURE_KEY = "summary_dashboard_signature"
LAST_CHANGE_KEY = "summary_last_change_at"
REFRESH_INTERVAL_SECONDS = 10
REFRESH_INTERVAL_LABEL = f"{REFRESH_INTERVAL_SECONDS}s"
SUMMARY_DATA_CACHE_KEY = "summary_last_valid_data"
SUMMARY_ERROR_KEY = "summary_database_error"

try:
    from _registration import register_listeningevents
except Exception as exc:
    register_listeningevents = None
    register_import_error = exc
else:
    register_import_error = None


def run_registration_with_feedback():
    """Run available listening-event registration with a spinner and display Spotify failures."""
    if register_listeningevents is None:
        return

    try:
        with st.spinner("Actualizando datos desde Spotify..."):
            result = register_listeningevents()
    except SpotifyError as exc:
        render_incident_notice(
            "summary_registration_error", exc, "No se pudieron actualizar los eventos de escucha."
        )
        return
    except Exception as exc:
        render_incident_notice(
            "summary_registration_error", exc, "No se pudo completar la sincronización."
        )
        return

    if result.status is SyncStatus.COMPLETED:
        clear_incident(st.session_state, "summary_registration_error")
        st.success("Datos de escucha actualizados.")
    else:
        error = RuntimeError(result.message or result.status.value)
        error.action = "sync listening events"
        error.retry_after = result.retry_after
        render_incident_notice(
            "summary_registration_error", error,
            f"Sincronización {result.status.value}: los datos locales pueden estar incompletos.",
        )


def run_metadata_enrichment(limit: int = 50):
    """Enrich one bounded batch using the shared optional metadata service."""
    from config import DATABASE_URL
    from services.enrichment import EnrichmentService

    service = EnrichmentService(
        DATABASE_URL,
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        musicbrainz_user_agent=os.getenv(
            "MUSICBRAINZ_USER_AGENT", "Spoty/0.1.0 (metadata enrichment via Streamlit)"
        ),
    )
    return service.enrich_pending(limit=limit)


def _render_metadata_enrichment_control():
    st.subheader("Metadatos externos")
    with st.form("metadata_enrichment_form"):
        limit = st.number_input(
            "Tracks por lote",
            min_value=1,
            max_value=100,
            value=50,
            step=10,
        )
        submitted = st.form_submit_button("Enriquecer tracks pendientes")

    if not submitted:
        return

    try:
        with st.spinner("Buscando metadata en fuentes externas..."):
            result = run_metadata_enrichment(limit=int(limit))
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "42P01":
            st.warning(
                "La migración de enriquecimiento aún no está aplicada. "
                "Ejecuta `python -m register.migrations` antes de usar esta opción."
            )
        else:
            render_incident_notice(
                "summary_metadata_enrichment_error",
                exc,
                "No se pudo iniciar el enriquecimiento.",
            )
        return

    if result.exit_code:
        failed_providers = ", ".join(
            f"{provider}: {count}"
            for provider, count in result.provider_errors.items()
            if count
        )
        st.warning(f"El lote terminó con errores de proveedor ({failed_providers}).")
        return

    clear_incident(st.session_state, "summary_metadata_enrichment_error")
    if result.candidate_tracks == 0:
        st.info("No hay tracks pendientes de enriquecimiento.")
    else:
        st.success(
            f"Lote revisado: {result.candidate_tracks} tracks; "
            f"{result.records_saved} registros nuevos guardados."
        )


def query_dashboard_signature() -> tuple[str, int, int, int, int, int, int]:
    """Read the latest event and table counts through the summary query service."""
    return SummaryQueryService().dashboard_signature()


def build_dashboard_signature(
    latest_track_id: str,
    latest_played_at: int,
    summary: pd.Series,
) -> tuple[str, int, int, int, int, int, int]:
    return (
        latest_track_id,
        latest_played_at,
        int(summary["total_tracks"]),
        int(summary["total_artists"]),
        int(summary["total_albums"]),
        int(summary["total_liked_tracks"]),
        int(summary["total_listening_events"]),
    )


def format_duration(duration_ms):
    """Format milliseconds as minutes and seconds, using a dash for missing durations."""
    if pd.isna(duration_ms):
        return "-"

    total_seconds = int(duration_ms / 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def get_image(images):
    """Return the first URL from a JSON image list or None for missing or invalid data."""
    if not images:
        return None

    try:
        parsed = json.loads(images)
        if isinstance(parsed, list) and parsed:
            return parsed[0]
    except (json.JSONDecodeError, TypeError):
        pass

    return None


@st.fragment(run_every=REFRESH_INTERVAL_LABEL)
def _watch_summary_changes_fragment():
    """Poll dashboard changes, record their local time, and rerun when the signature changes."""
    try:
        current_signature = query_dashboard_signature()
    except Exception as exc:
        record_incident(st.session_state, SUMMARY_ERROR_KEY, exc)
        return
    clear_incident(st.session_state, SUMMARY_ERROR_KEY)

    previous_signature = st.session_state.get(SIGNATURE_KEY)
    if previous_signature is None:
        st.session_state[SIGNATURE_KEY] = current_signature
        return

    if current_signature != previous_signature:
        st.session_state[SIGNATURE_KEY] = current_signature
        st.session_state[LAST_CHANGE_KEY] = datetime.now(LOCAL_TIMEZONE).strftime(
            "%H:%M:%S"
        )
        st.rerun()


def _render_spotify_dashboard():
    """Query and render the latest listening event, catalog totals, and recent listening history."""
    auth_available = has_spotify_auth_available()

    if register_listeningevents is None:
        st.caption(
            "Registro disponible solo si el repo está configurado correctamente."
        )
        if register_import_error:
            st.caption(f"Detalle de importación: {register_import_error}")
    elif not auth_available:
        render_spotify_authorization_section()

    _render_metadata_enrichment_control()

    queries = SummaryQueryService()
    cached_data = st.session_state.get(SUMMARY_DATA_CACHE_KEY)
    stale = False
    try:
        rows = queries.recent_listening_events(limit=100)
        summary_data = queries.library_counts()
        artist_ids = set()
        for row in rows:
            try:
                ids = json.loads(row.get("artists_ids") or "[]")
                if isinstance(ids, list):
                    artist_ids.update(artist_id for artist_id in ids if artist_id)
            except (json.JSONDecodeError, TypeError):
                continue
        artist_names = queries.artist_names(list(artist_ids))
        cached_data = {
            "rows": rows,
            "summary": summary_data,
            "artist_names": artist_names,
            "saved_at": datetime.now(LOCAL_TIMEZONE),
        }
        st.session_state[SUMMARY_DATA_CACHE_KEY] = cached_data
        clear_incident(st.session_state, SUMMARY_ERROR_KEY)
    except Exception as exc:
        stale = True
        render_incident_notice(
            SUMMARY_ERROR_KEY, exc, "No se pudo actualizar el resumen; se conserva el último dato válido."
        )
        if cached_data is None:
            st.caption("Todavía no hay datos de resumen almacenados para mostrar.")
            return

    if stale:
        st.caption(
            "Datos desactualizados desde "
            f"{cached_data['saved_at'].astimezone(LOCAL_TIMEZONE).strftime('%H:%M:%S')}"
        )

    df = pd.DataFrame(cached_data["rows"])
    summary = pd.Series(cached_data["summary"])
    artist_names = cached_data["artist_names"]

    latest_track_id = ""
    latest_played_at = 0
    if not df.empty:
        latest_track_id = str(df.iloc[0]["track_id"] or "")
        latest_played_at = int(df.iloc[0]["played_at"] or 0)

    st.session_state[SIGNATURE_KEY] = build_dashboard_signature(
        latest_track_id=latest_track_id,
        latest_played_at=latest_played_at,
        summary=summary,
    )

    if df.empty:
        st.caption("No hay listening events registrados todavía.")
        return

    def get_artists(value):
        """Resolve JSON artist IDs to names, retaining unknown IDs and using a dash for invalid data."""
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


def render_summary_page():
    """Render the summary dashboard and start its periodic database change watcher."""
    _watch_summary_changes_fragment()
    _render_spotify_dashboard()
