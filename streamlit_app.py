from __future__ import annotations

import os
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import altair as alt
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

RECENT_LISTENING_LIMIT = 50
MONTH_NAMES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


@st.cache_data(ttl=300)
def load_snapshot(token: str | None) -> SpotySnapshot:
    return GitHubDataReader(token=token).load()


def _track_name(track_id: str, tracks: dict[str, dict]) -> str:
    track = tracks.get(track_id)
    return track.get("name") or track_id if track else track_id


def _shift_calendar_month(offset: int) -> None:
    year, month = divmod(
        st.session_state["calendar_year"] * 12
        + st.session_state["calendar_month"] - 1 + offset,
        12,
    )
    st.session_state["calendar_year"] = year
    st.session_state["calendar_month"] = month + 1


def render_listening_calendar(snapshot: SpotySnapshot) -> None:
    st.subheader("Calendario de escuchas")
    if not snapshot.listening_events:
        st.info("Todavia no hay escuchas registradas para mostrar el calendario.")
        return

    daily_plays = Counter(
        datetime.fromtimestamp(event["played_at"], timezone.utc).date()
        for event in snapshot.listening_events
    )
    first_month = min(daily_plays).replace(day=1)
    last_month = max(daily_plays).replace(day=1)
    year = min(
        max(st.session_state.get("calendar_year", last_month.year), first_month.year),
        last_month.year,
    )
    start_month = first_month.month if year == first_month.year else 1
    end_month = last_month.month if year == last_month.year else 12
    month = min(
        max(st.session_state.get("calendar_month", last_month.month), start_month),
        end_month,
    )
    st.session_state["calendar_year"] = year
    st.session_state["calendar_month"] = month
    first_day = date(year, month, 1)

    with st.container(horizontal=True, wrap=False, vertical_alignment="center"):
        st.button(
            "←", key="calendar_previous", help="Mes anterior",
            on_click=_shift_calendar_month, args=(-1,),
            disabled=first_day == first_month,
        )
        st.markdown(
            f"### {MONTH_NAMES[month - 1]} {year}",
            text_alignment="center", width="stretch",
        )
        st.button(
            "→", key="calendar_next", help="Mes siguiente",
            on_click=_shift_calendar_month, args=(1,),
            disabled=first_day == last_month,
        )
    selectors = st.columns([1, 2])
    with selectors[0]:
        st.selectbox(
            "Año", options=range(last_month.year, first_month.year - 1, -1),
            key="calendar_year",
        )
    with selectors[1]:
        st.selectbox(
            "Mes (UTC)", options=range(start_month, end_month + 1),
            format_func=lambda value: MONTH_NAMES[value - 1], key="calendar_month",
        )

    weekdays = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
    rows = []
    for offset in range(monthrange(first_day.year, first_day.month)[1]):
        day = first_day + timedelta(days=offset)
        rows.append({
            "Fecha": day.isoformat(),
            "Dia": day.day,
            "DiaSemana": weekdays[day.weekday()],
            "Semana": (first_day.weekday() + offset) // 7,
            "Escuchas": daily_plays[day],
        })

    maximum = max(daily_plays.values())
    chart = alt.Chart(pd.DataFrame(rows)).encode(
        x=alt.X(
            "DiaSemana:N", sort=weekdays, title=None,
            axis=alt.Axis(orient="top", labelAngle=0),
        ),
        y=alt.Y("Semana:O", sort="ascending", axis=None, title=None),
        tooltip=[
            alt.Tooltip("Fecha:N", title="Fecha (UTC)"),
            alt.Tooltip("Escuchas:Q", title="Escuchas registradas", format="d"),
        ],
    )
    cells = chart.mark_rect(cornerRadius=4, stroke="white", strokeWidth=2).encode(
        color=alt.condition(
            alt.datum.Escuchas == 0,
            alt.value("#000000"),
            alt.Color(
                "Escuchas:Q", title="Escuchas",
                scale=alt.Scale(
                    domain=[1, maximum], zero=False, clamp=True,
                    range=["#22c55e", "#ef4444" if maximum > 1 else "#22c55e"],
                    interpolate="hsl",
                ),
                legend=alt.Legend(orient="bottom", tickMinStep=1, format="d"),
            ),
        ),
    )
    labels = chart.mark_text(fontSize=14).encode(
        text=alt.Text("Dia:O"),
        color=alt.condition(
            alt.datum.Escuchas == 0, alt.value("white"), alt.value("#111827"),
        ),
    )
    st.caption(
        "Cada casilla es un dia (UTC). La escala va de verde (1 escucha) a rojo "
        f"(maximo historico diario: {maximum}). La escala es la misma en todos los meses. "
        "Pasa el cursor para ver el recuento. Negro: 0 registros, "
        "lo que no implica necesariamente que no hayas escuchado musica."
    )
    st.altair_chart((cells + labels).properties(height=280), width="stretch")


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
    columns[1].metric("Canciones en catálogo", f"{len(snapshot.tracks):,}")
    columns[2].metric("Álbumes en catálogo", f"{len(snapshot.albums):,}")
    columns[3].metric("Artistas en catálogo", f"{len(snapshot.artists):,}")
    st.caption(f"Ultima escucha registrada: {latest_text}")

    render_listening_calendar(snapshot)

    top_tracks = plays.most_common(10)
    if top_tracks:
        ranking = []
        for position, (track_id, count) in enumerate(top_tracks, start=1):
            name = _track_name(track_id, tracks)
            artist_names = ", ".join(
                artists.get(artist_id) or artist_id
                for artist_id in tracks.get(track_id, {}).get("artists_ids") or []
            )
            label = f"{position}. {name}"
            if artist_names:
                label += f" · {artist_names}"
            ranking.append({
                "Etiqueta": label,
                "Cancion": name,
                "Artista": artist_names,
                "Escuchas": count,
            })
        first = min(row["played_at"] for row in snapshot.listening_events)
        start_date = datetime.fromtimestamp(first, timezone.utc).strftime("%d/%m/%Y")
        end_date = datetime.fromtimestamp(latest["played_at"], timezone.utc).strftime("%d/%m/%Y")
        st.subheader("Top 10 canciones por escuchas registradas")
        st.caption(
            f"Todo el historial guardado: {start_date} – {end_date} (UTC). "
            "Cada barra indica el numero de reproducciones registradas de una cancion; "
            "no son minutos escuchados ni solo las escuchas recientes."
        )
        chart = alt.Chart(pd.DataFrame(ranking)).encode(
            x=alt.X(
                "Escuchas:Q", title="Reproducciones registradas",
                scale=alt.Scale(domain=[0, top_tracks[0][1] + 1]),
                axis=alt.Axis(tickMinStep=1, format="d"),
            ),
            y=alt.Y(
                "Etiqueta:N", title=None,
                sort=[row["Etiqueta"] for row in ranking],
                axis=alt.Axis(labelLimit=450),
            ),
            tooltip=[
                alt.Tooltip("Cancion:N", title="Cancion"),
                alt.Tooltip("Artista:N", title="Artista"),
                alt.Tooltip("Escuchas:Q", title="Reproducciones", format="d"),
            ],
        )
        bars = chart.mark_bar(color="#1DB954", cornerRadiusEnd=4)
        counts = chart.mark_text(align="left", dx=6).encode(text=alt.Text("Escuchas:Q", format="d"))
        st.altair_chart((bars + counts).properties(height=36 * len(ranking)), width="stretch")

    recent = sorted(
        snapshot.listening_events,
        key=lambda row: row["played_at"],
        reverse=True,
    )[:RECENT_LISTENING_LIMIT]
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
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=600)


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
