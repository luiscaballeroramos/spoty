import os

import streamlit as st


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
    from app_views.reproduction import (
        refresh_playback_on_page_entry,
        render_reproduction_page,
    )
    from app_views.summary import render_summary_page, run_registration_with_feedback
    from app_views.top_navigation import render_top_navigation
    from spotifyapi.streamlit_auth import process_spotify_oauth_callback
except Exception as exc:
    st.error(f"No se pudo iniciar la app ({type(exc).__name__}): {exc}")
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
        background: #ffffff !important;
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
PAGE_OPTIONS = {PAGE_SUMMARY, PAGE_REPRODUCTION}
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


def get_current_page() -> str:
    page_name = st.session_state.get(PAGE_STATE_KEY, PAGE_REPRODUCTION)
    return page_name


def render_current_page():
    current_page = get_current_page()
    previous_page = st.session_state.get(PREVIOUS_PAGE_STATE_KEY)

    render_top_navigation(
        current_page=current_page,
        on_summary_click=go_to_summary_page,
        on_reproduction_click=go_to_reproduction_page,
    )

    if current_page == PAGE_REPRODUCTION:
        if previous_page != PAGE_REPRODUCTION:
            refresh_playback_on_page_entry()
        render_reproduction_page()
    elif current_page == PAGE_SUMMARY:
        render_summary_page()
    else:
        raise ValueError(f"Unknown page: {current_page}")

    st.session_state[PREVIOUS_PAGE_STATE_KEY] = current_page


process_spotify_oauth_callback()
render_current_page()
