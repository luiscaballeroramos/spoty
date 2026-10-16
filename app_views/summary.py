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

    total_listens = analysis["total_listens"]
    classified_listens = analysis["classified_listens"]
    coverage = 100 * classified_listens / total_listens if total_listens else 0
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
        .genre-heading {
            margin: 0;
            color: #171a17;
            font-family: Georgia, "Times New Roman", serif;
            font-size: 24px;
            font-weight: 400;
        }
        .genre-coverage {
            margin: 5px 0 20px;
            color: #626262;
            font-size: 13px;
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
            .genre-heading { font-size: 21px; }
        }
        </style>
        """ + '<section class="genre-infographic">'
        '<h2 class="genre-heading">Géneros más escuchados</h2>'
        f'<p class="genre-coverage">{_format_count(classified_listens)} de '
        f'{_format_count(total_listens)} escuchas con género identificado · '
        f'{_format_percent(coverage)}% de cobertura</p>'
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

    metrics = (
        ("♫", counts["total_tracks"]),
        ("👤", counts["total_artists"]),
        ("💿", counts["total_albums"]),
        ("🎧", counts["total_listening_events"]),
    )
    items = "".join(
        '<article class="library-stat">'
        f'<span class="library-stat-icon" aria-hidden="true">{symbol}</span>'
        f'<strong class="library-stat-value">{_format_count(value)}</strong>'
        "</article>"
        for symbol, value in metrics
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
            font-family: Georgia, "Times New Roman", serif;
            color: #171a17;
            font-size: 32px;
            font-weight: 400;
            line-height: 1;
            overflow-wrap: anywhere;
            font-variant-numeric: tabular-nums;
        }
        .library-stat-icon {
            font-size: 24px;
            line-height: 1;
        }
        @media (max-width: 680px) {
            .library-infographic { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .library-stat-value { font-size: 30px; }
        }
        </style>
        """ + f'<section class="library-infographic">{items}</section>',
        unsafe_allow_html=True,
    )

    try:
        genre_analysis = queries.top_listened_genres()
        clear_incident(st.session_state, "summary_genre_error")
    except Exception as exc:
        render_incident_notice(
            "summary_genre_error", exc, "No se pudo calcular el resumen de géneros."
        )
        return
    _render_genre_infographic(genre_analysis)
