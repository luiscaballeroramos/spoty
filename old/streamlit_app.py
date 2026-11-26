import os

import streamlit as st


# Select before config/.env, secrets, OAuth or automatic captures can load.
os.environ.setdefault("SPOTY_SUMMARY_BACKEND", "sql")
if os.environ["SPOTY_SUMMARY_BACKEND"].strip() != "sql":
    from app_views.summary import render_summary_page
    from app_views.summary_source import load_local_summary_snapshot
    from spoty.services.summary_queries import SummaryQueryService as CsvSummaryQueryService

    st.set_page_config(page_title="luiSPOTY · CSV local", page_icon="🎵", layout="wide")
    csv_page = st.radio(
        "Sección",
        ("Resumen", "Metadatos", "Pulse", "Historial"),
        horizontal=True,
        key="csv_read_only_page",
    )
    if csv_page == "Resumen":
        render_summary_page()
    elif csv_page == "Metadatos":
        from app_views.error_feedback import render_incident_notice
        from app_views.csv_favorite_status import render_csv_favorite_status
        from app_views.metadata import render_metadata_page

        try:
            snapshot = load_local_summary_snapshot()
            st.info(
                f"CSV local · Solo lectura · Cuenta: {snapshot.account} · "
                f"Commit: {snapshot.commit_id}"
            )
            st.caption("Las capturas y las demás páginas de producción están desactivadas.")
            if snapshot.manifest is not None:
                st.caption(f"Snapshot consolidado: {snapshot.manifest['published_at']}")
            st.caption(
                f"Lotes pendientes: {snapshot.pending_batches()}. "
                "No incluidos en los totales de cobertura."
            )
            render_csv_favorite_status(snapshot)
            render_metadata_page(query_service=CsvSummaryQueryService(snapshot))
        except Exception as exc:
            render_incident_notice(
                "metadata_database_error", exc, "No se pudo cargar el resumen de metadatos."
            )
    elif csv_page == "Pulse":
        from app_views.pulse import render_csv_pulse_page
        from app_views.csv_favorite_status import render_csv_favorite_status

        try:
            snapshot = load_local_summary_snapshot()
            st.info(
                f"CSV local · Solo lectura · Cuenta: {snapshot.account} · "
                f"Commit: {snapshot.commit_id}"
            )
            st.caption("Las capturas y las demás páginas de producción están desactivadas.")
            if snapshot.manifest is not None:
                st.caption(f"Snapshot consolidado: {snapshot.manifest['published_at']}")
            st.caption(
                f"Lotes pendientes: {snapshot.pending_batches()}. "
                "No incluidos en el historial consolidado."
            )
            render_csv_favorite_status(snapshot)
            render_csv_pulse_page(CsvSummaryQueryService(snapshot))
        except Exception as exc:
            from app_views.error_feedback import render_incident_notice

            render_incident_notice(
                "pulse_history_error", exc, "No se pudo cargar el historial Pulse del snapshot."
            )
    else:
        from app_views.error_feedback import render_incident_notice
        from app_views.csv_favorite_status import render_csv_favorite_status
        from app_views.listening_history import render_csv_listening_history

        try:
            snapshot = load_local_summary_snapshot()
            st.info(
                f"CSV local · Solo lectura · Cuenta: {snapshot.account} · "
                f"Commit: {snapshot.commit_id}"
            )
            st.caption("La reproducción y los cambios de favoritos requieren el modo SQL/live.")
            if snapshot.manifest is not None:
                st.caption(f"Snapshot consolidado: {snapshot.manifest['published_at']}")
            st.caption(
                f"Lotes pendientes: {snapshot.pending_batches()}. "
                "No incluidos en el historial."
            )
            render_csv_favorite_status(snapshot)
            render_csv_listening_history(CsvSummaryQueryService(snapshot))
        except Exception as exc:
            render_incident_notice(
                "listening_history_error", exc, "No se pudo cargar el historial de escuchas."
            )
    st.stop()


# Load environment variables from Streamlit secrets if available
# Database URL is expected to be in:
# - st.secrets["DATABASE_URL"]
# - st.secrets["connections"]["neon"]["url"]

def load_streamlit_secrets_to_env():
    try:
        secrets = st.secrets
    except Exception:
        return

    for name in (
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_REDIRECT_URI",
        "SPOTIFY_REFRESH_TOKEN",
        "LASTFM_API_KEY",
    ):
        try:
            value = secrets[name]
        except Exception:
            value = None
        if value:
            os.environ[name] = str(value).strip()

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

st.set_page_config(
    page_title="luiSPOTY",
    page_icon="🎵",
    layout="wide",
)


try:
    from app_views.metadata import render_metadata_page
    from app_views.pulse import render_pulse_page
    from app_views.listening_event_capture import render_listening_event_capture
    from app_views.reproduction import (
        refresh_playback_on_page_entry,
        render_reproduction_page,
    )
    from app_views.summary import render_summary_page, run_registration_with_feedback
    from app_views.top_navigation import render_top_navigation
    from spotifyapi.streamlit_auth import process_spotify_oauth_callback
except Exception:
    st.stop()

st.markdown(
    """
    <style>
    html, body, .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    .block-container {
        background: #ffffff !important;
    }

    body, .stApp, [data-testid="stMainBlockContainer"],
    [data-testid="stMainBlockContainer"] div,
    [data-testid="stMarkdownContainer"],
    .element-container {
        color: #111111 !important;
    }

    p, h1, h2, h3, h4, h5, h6, span, label, li, a,
    strong, em, small {
        color: inherit !important;
    }

    .stButton > button,
    .stSelectbox > div,
    .stTextInput > div,
    .stNumberInput > div,
    .stTextArea > div,
    .stDateInput > div,
    .stTimeInput > div,
    .stForm,
    .stTabs [role="tablist"],
    [data-testid="stDataFrame"],
    [data-testid="stTable"] {
        background-color: #ffffff !important;
        border: 1px solid #000000 !important;
        border-radius: 0.5rem !important;
    }

    [data-testid="stHeader"],
    [data-testid="stDecoration"],
    [data-testid="stToolbar"] {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

PAGE_SUMMARY = "summary"
PAGE_REPRODUCTION = "reproduction"
PAGE_METADATA = "metadata"
PAGE_PULSE = "pulse"
PAGE_OPTIONS = {PAGE_SUMMARY, PAGE_REPRODUCTION, PAGE_PULSE, PAGE_METADATA}
PAGE_STATE_KEY = "spoty_page"
PREVIOUS_PAGE_STATE_KEY = "spoty_previous_page"


def go_to_page(page_name: str):
    st.session_state[PAGE_STATE_KEY] = page_name
    st.rerun()


def go_to_summary_page():
    if get_current_page() != PAGE_SUMMARY:
        run_registration_with_feedback()
    go_to_page(PAGE_SUMMARY)


def go_to_reproduction_page():
    refresh_playback_on_page_entry()
    go_to_page(PAGE_REPRODUCTION)


def go_to_metadata_page():
    go_to_page(PAGE_METADATA)


def go_to_pulse_page():
    refresh_playback_on_page_entry()
    go_to_page(PAGE_PULSE)


def get_current_page() -> str:
    page_name = st.session_state.get(PAGE_STATE_KEY, PAGE_REPRODUCTION)
    return page_name


def render_current_page():
    current_page = get_current_page()
    previous_page = st.session_state.get(PREVIOUS_PAGE_STATE_KEY)

    render_listening_event_capture()
    render_top_navigation(
        current_page=current_page,
        on_summary_click=go_to_summary_page,
        on_reproduction_click=go_to_reproduction_page,
        on_metadata_click=go_to_metadata_page,
        on_pulse_click=go_to_pulse_page,
    )

    if current_page == PAGE_REPRODUCTION:
        if previous_page != PAGE_REPRODUCTION:
            refresh_playback_on_page_entry()
        render_reproduction_page()
    elif current_page == PAGE_SUMMARY:
        render_summary_page()
    elif current_page == PAGE_METADATA:
        render_metadata_page()
    elif current_page == PAGE_PULSE:
        render_pulse_page()
    else:
        raise ValueError(f"Unknown page: {current_page}")

    st.session_state[PREVIOUS_PAGE_STATE_KEY] = current_page


try:
    process_spotify_oauth_callback()
    render_current_page()
except Exception:
    st.stop()
