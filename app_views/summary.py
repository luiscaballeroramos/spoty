from html import escape

import streamlit as st

from app_views.error_feedback import (
    clear_incident,
    render_incident_notice,
)
from services.summary_queries import SummaryQueryService
from services.synchronization import SyncStatus
from spotifyapi.errors import SpotifyError

try:
    from _registration import register_listeningevents
except Exception as exc:
    register_listeningevents = None
    register_import_error = exc
else:
    register_import_error = None


def run_registration_with_feedback():
    """Capture recent Spotify listening events in the local CSV and report the result."""
    if register_listeningevents is None:
        return

    try:
        with st.spinner("Actualizando datos desde Spotify..."):
            result = register_listeningevents()
    except SpotifyError as exc:
        render_incident_notice(
            "summary_registration_error",
            exc,
            "No se pudieron consultar las escuchas de Spotify.",
        )
        return
    except Exception as exc:
        render_incident_notice(
            "summary_registration_error",
            exc,
            "No se pudo completar la captura local de escuchas.",
        )
        return

    if result.status is SyncStatus.COMPLETED:
        clear_incident(st.session_state, "summary_registration_error")
        st.success(
            "Escuchas guardadas en local: "
            f"{result.counters.get('saved_local_events', 0)} nuevas; "
            f"{result.counters.get('skipped_existing_listening_events', 0)} repetidas omitidas."
        )
    else:
        error = RuntimeError(result.message or result.status.value)
        error.action = "sync listening events"
        error.retry_after = result.retry_after
        render_incident_notice(
            "summary_registration_error", error,
            f"Captura local {result.status.value}: el CSV puede estar incompleto.",
        )


def _format_count(value: int) -> str:
    """Format a count with dots as thousands separators."""
    return f"{int(value):,}".replace(",", ".")


def _format_percent(value: float) -> str:
    """Format a percentage with a comma decimal separator."""
    return f"{value:.1f}".replace(".", ",")


def _render_genre_infographic(analysis: dict):
    if not analysis["genres"]:
        st.caption("Aún no hay géneros disponibles para analizar las escuchas.")
        return

    max_share = max(genre["share_percent"] for genre in analysis["genres"])
    rows = []
    for genre in analysis["genres"]:
        share = genre["share_percent"]
        width = 100 * share / max_share if max_share else 0
        rows.append(
            '<div class="genre-row">'
            '<div class="genre-row-heading">'
            f'<span class="genre-name">{escape(genre["genre"].title())}</span>'
            f'<strong class="genre-share">{_format_percent(share)}%</strong>'
            '</div>'
            '<div class="genre-track" aria-hidden="true">'
            f'<span class="genre-fill" style="width:{width:.1f}%"></span>'
            '</div>'
            '</div>'
        )

    st.markdown(
        """
        <style>
        .genre-infographic {
            max-width: 760px;
            margin-top: 28px;
            padding-top: 20px;
            border-top: 1px solid #d8d8d8;
        }
        .genre-list { display: grid; gap: 14px; }
        .genre-row-heading {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 5px;
        }
        .genre-name {
            min-width: 0;
            color: #202020;
            font-size: 14px;
            overflow-wrap: anywhere;
        }
        .genre-share {
            flex: 0 0 auto;
            color: #202020;
            font-size: 13px;
            font-variant-numeric: tabular-nums;
        }
        .genre-track {
            height: 7px;
            overflow: hidden;
            border-radius: 4px;
            background: #ededed;
        }
        .genre-fill {
            display: block;
            height: 100%;
            border-radius: inherit;
            background: #bd553d;
        }
        .genre-row:nth-child(5n + 2) .genre-fill { background: #b9922f; }
        .genre-row:nth-child(5n + 3) .genre-fill { background: #4e856c; }
        .genre-row:nth-child(5n + 4) .genre-fill { background: #397c94; }
        .genre-row:nth-child(5n + 5) .genre-fill { background: #9a607e; }
        @media (max-width: 600px) {
            .genre-infographic { margin-top: 20px; padding-top: 16px; }
        }
        </style>
        """ + '<section class="genre-infographic">'
        f'<div class="genre-list">{"".join(rows)}</div>'
        '</section>',
        unsafe_allow_html=True,
    )


def render_summary_page():
    """Render only the four fixed library totals as a responsive infographic."""
    try:
        queries = SummaryQueryService()
        counts = queries.library_counts()
        clear_incident(st.session_state, "summary_database_error")
    except Exception as exc:
        render_incident_notice(
            "summary_database_error", exc, "No se pudo cargar el resumen de la biblioteca."
        )
        return

    icons = (
        '<svg viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
        '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
        '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/></svg>',
        '<svg viewBox="0 0 24 24"><path d="M3 14v-2a9 9 0 0 1 18 0v2"/><rect x="3" y="12" width="4" height="8" rx="2"/><rect x="17" y="12" width="4" height="8" rx="2"/></svg>',
    )
    values = (
        counts["total_tracks"],
        counts["total_artists"],
        counts["total_albums"],
        counts["total_listening_events"],
    )
    items = "".join(
        '<article class="library-stat">'
        f'<span class="library-stat-icon" aria-hidden="true">{icon}</span>'
        f'<strong class="library-stat-value">{int(value)}</strong>'
        "</article>"
        for icon, value in zip(icons, values)
    )

    st.markdown(
        """
        <style>
        .library-infographic {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
            margin: 2rem 0 0;
        }
        .library-stat {
            display: flex;
            min-width: 0;
            align-items: center;
            justify-content: center;
            gap: 10px;
            padding: 12px 4px;
        }
        .library-stat-value {
            display: block;
            color: #171a17;
            font-size: 24px;
            font-weight: 500;
            line-height: 1;
            overflow-wrap: anywhere;
            font-variant-numeric: tabular-nums;
        }
        .library-stat-icon {
            display: flex;
            width: 22px;
            height: 22px;
            flex: 0 0 22px;
            color: #171a17;
        }
        .library-stat-icon svg {
            width: 100%;
            height: 100%;
            fill: none;
            stroke: currentColor;
            stroke-linecap: round;
            stroke-linejoin: round;
            stroke-width: 1.7;
        }
        .st-key-summary_genre_toolbar {
            max-width: 760px;
            flex-wrap: nowrap;
            margin-top: 24px;
        }
        .st-key-summary_genre_toolbar [data-testid="stMarkdownContainer"] {
            margin-bottom: 0;
        }
        .genre-coverage {
            margin: 0;
            color: #626262;
            font-size: 13px;
            line-height: 1.2;
            white-space: nowrap;
            font-variant-numeric: tabular-nums;
        }
        .st-key-summary_genre_mode [role="radio"] {
            color: #111111 !important;
            background: #ffffff !important;
            border-color: #111111 !important;
        }
        .st-key-summary_genre_mode [role="radio"][aria-checked="true"] {
            color: #ffffff !important;
            background: #111111 !important;
        }
        .st-key-summary_genre_mode [role="radio"][aria-checked="true"] * {
            color: #ffffff !important;
        }
        @media (max-width: 680px) {
            .library-infographic { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .library-stat-value { font-size: 22px; }
        }
        </style>
        """ + f'<section class="library-infographic">{items}</section>',
        unsafe_allow_html=True,
    )

    with st.container(horizontal=True, vertical_alignment="center", gap="medium", key="summary_genre_toolbar"):
        genre_grouping = st.segmented_control(
            "Géneros", ["Géneros", "Subgéneros"], default="Géneros",
            selection_mode="single", key="summary_genre_mode",
            label_visibility="collapsed", width="content",
        )
        with st.container(width="content"):
            coverage_slot = st.empty()
    try:
        genre_analysis = queries.top_listened_genres(
            grouping="labels" if genre_grouping == "Subgéneros" else "families",
        )
        clear_incident(st.session_state, "summary_genre_error")
    except Exception as exc:
        render_incident_notice(
            "summary_genre_error", exc, "No se pudo calcular el resumen de géneros."
        )
        return
    total_listens = genre_analysis["total_listens"]
    classified_listens = genre_analysis["classified_listens"]
    coverage = 100 * classified_listens / total_listens if total_listens else 0
    coverage_slot.markdown(
        f'<p class="genre-coverage" title="{_format_count(classified_listens)} / '
        f'{_format_count(total_listens)} escuchas incluidas">{_format_percent(coverage)}%</p>',
        unsafe_allow_html=True,
    )
    _render_genre_infographic(genre_analysis)
