import altair as alt
import pandas as pd
import streamlit as st

from app_views.error_feedback import clear_incident, render_incident_notice
from services.summary_queries import SummaryQueryService


@st.cache_data(ttl=60, show_spinner=False)
def _load_metadata_overview(entity_type: str):
    return SummaryQueryService().metadata_overview(entity_type)


def _coverage_chart(rows, total, label, colors):
    data = pd.DataFrame([
        {
            label: row["label"],
            "Disponibilidad": status,
            "Entidades": count,
            "Porcentaje": 100 * count / total if total else 0,
        }
        for row in rows
        for status, count in (
            ("Disponible", row["available"]),
            ("Sin datos", total - row["available"]),
        )
    ])
    chart = alt.Chart(data).mark_bar(size=18).encode(
        x=alt.X("Porcentaje:Q", stack="zero", scale=alt.Scale(domain=[0, 100]),
            axis=alt.Axis(tickCount=6), title="Cobertura (%)"),
        y=alt.Y(f"{label}:N", sort=[row["label"] for row in rows], title=None,
            axis=alt.Axis(labelLimit=180, labelOverlap=False)),
        color=alt.Color("Disponibilidad:N", scale=alt.Scale(
            domain=["Disponible", "Sin datos"], range=colors), title=None),
        order=alt.Order("Disponibilidad:N", sort="ascending"),
        tooltip=[label, "Disponibilidad", "Entidades", alt.Tooltip("Porcentaje:Q", format=".1f")],
    ).properties(height=max(180, len(rows) * 32 + 90), background="#ffffff").configure_axis(
        labelColor="#303030", titleColor="#303030", gridColor="#ededed",
        labelFontSize=12, titleFontSize=12,
    ).configure_legend(labelColor="#303030", labelFontSize=12).configure_view(stroke=None)
    st.altair_chart(chart, width="stretch", theme=None)


def render_metadata_page():
    """Display metadata coverage for tracks, artists and albums without refreshing providers."""
    st.title("Metadatos")
    entity_type = st.selectbox(
        "Entidad", ("tracks", "artists", "albums"),
        format_func={"tracks": "Tracks", "artists": "Artists", "albums": "Albums"}.get,
        key="metadata_entity_type",
    )
    try:
        with st.spinner("Cargando metadatos..."):
            overview = _load_metadata_overview(entity_type)
        clear_incident(st.session_state, "metadata_database_error")
    except Exception as exc:
        render_incident_notice(
            "metadata_database_error", exc, "No se pudo cargar el resumen de metadatos."
        )
        return

    total = overview["total"]
    enriched = overview["enriched"]
    total_column, enriched_column, coverage_column = st.columns(3)
    total_column.metric("En el catalogo", f"{total:,}".replace(",", "."))
    enriched_column.metric("Con enriquecimiento", f"{enriched:,}".replace(",", "."))
    coverage_column.metric("Cobertura de enriquecimiento", f"{100 * enriched / total if total else 0:.1f}%")
    if not total:
        st.info("No hay entidades de este tipo en el catalogo.")
        return

    st.subheader("Disponibilidad por fuente")
    _coverage_chart(
        [{"label": source["source"], "available": source["available"]} for source in overview["sources"]],
        total, "Fuente", ["#397c94", "#ededed"],
    )

    sources = {source["source"]: source for source in overview["sources"]}
    source_name = st.selectbox(
        "Fuente", list(sources), key=f"metadata_source_{entity_type}",
        index=1 if sources["SmarterPlaylists"]["fields"] else 0,
    )
    fields = sources[source_name]["fields"]
    st.subheader("Disponibilidad por campo")
    if not fields:
        st.info("No hay metadatos disponibles de esta fuente.")
        return

    _coverage_chart(
        [{"label": field["field"], "available": field["available"]} for field in fields],
        total, "Campo", ["#4e856c", "#ededed"],
    )
    with st.expander("Detalle de cobertura"):
        st.dataframe(pd.DataFrame([
            {"Campo": field["field"], "Disponibles": field["available"],
             "Sin datos": total - field["available"],
             "Cobertura (%)": round(100 * field["available"] / total, 1)}
            for field in fields
        ]), hide_index=True, width="stretch")
