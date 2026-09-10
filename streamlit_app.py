import os

import streamlit as st
from app_views.home import render_home_page
from app_views.reproduction import render_reproduction_page
from app_views.summary import render_summary_page
from app_views.top_navigation import render_top_navigation
from spotifyapi.streamlit_auth import process_spotify_oauth_callback


# Load environment variables from Streamlit secrets if available
# Database URL is expected to be in:
# - st.secrets["DATABASE_URL"]
# - st.secrets["connections"]["neon"]["url"]

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

st.set_page_config(
    page_title="luiSPOTY",
    page_icon="🎵",
    layout="wide",
)

PAGE_HOME = "home"
PAGE_SUMMARY = "summary"
PAGE_REPRODUCTION = "reproduction"
PAGE_OPTIONS = {PAGE_HOME, PAGE_SUMMARY, PAGE_REPRODUCTION}
PAGE_STATE_KEY = "spoty_page"


def go_to_page(page_name: str):
    st.session_state[PAGE_STATE_KEY] = page_name
    st.rerun()


def go_to_summary_page():
    go_to_page(PAGE_SUMMARY)


def go_to_reproduction_page():
    go_to_page(PAGE_REPRODUCTION)


def go_to_home_page():
    go_to_page(PAGE_HOME)


def get_current_page() -> str:
    page_name = st.session_state.get(PAGE_STATE_KEY, PAGE_HOME)
    if page_name not in PAGE_OPTIONS:
        return PAGE_HOME
    return page_name


def render_current_page():
    current_page = get_current_page()

    render_top_navigation(
        current_page=current_page,
        on_home_click=go_to_home_page,
        on_summary_click=go_to_summary_page,
        on_reproduction_click=go_to_reproduction_page,
    )

    if current_page == PAGE_REPRODUCTION:
        render_reproduction_page()
    elif current_page == PAGE_SUMMARY:
        render_summary_page()
    else:
        render_home_page(
            on_reproduction_click=go_to_reproduction_page,
            on_summary_click=go_to_summary_page,
        )


process_spotify_oauth_callback()
render_current_page()
