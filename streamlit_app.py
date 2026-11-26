from __future__ import annotations

import os
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from layouts import Layout, render_layout
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
HORIZONTAL_DASHBOARD = Layout(
    areas=(
        ("metrics", "metrics"),
        ("growth", "growth"),
        ("calendar", "ranking"),
        ("recent", "recent"),
    ),
    columns=(1, 1),
    rows=(1, 1, 1, 1),
    min_height=0,
)
VERTICAL_DASHBOARD = Layout(
    areas=(("metrics",), ("growth",), ("calendar",), ("ranking",), ("recent",)),
    columns=(1,),
    rows=(1, 1, 1, 1, 1),
    min_height=0,
)


@st.cache_data(ttl=300)
def load_snapshot(token: str | None) -> SpotySnapshot:
    return GitHubDataReader(token=token).load()


def _track_name(track_id: str, tracks: dict[str, dict]) -> str:
    track = tracks.get(track_id)
    return track.get("name") or track_id if track else track_id


def listening_growth(snapshot: SpotySnapshot, start: date, end: date) -> pd.DataFrame:
    tracks = {track["id"]: track for track in snapshot.tracks}
    daily = Counter()
    first_seen: dict[str, dict[str, date]] = {
        "Canciones": {}, "Artistas": {}, "Álbumes": {},
    }
    for event in snapshot.listening_events:
        day = datetime.fromtimestamp(event["played_at"], timezone.utc).date()
        if day > end:
            continue
        daily[day] += 1
        track_id = event["track_id"]
        track = tracks.get(track_id, {})
        entities = {
            "Canciones": (track_id,),
            "Artistas": track.get("artists_ids") or (),
            "Álbumes": (track["album_id"],) if track.get("album_id") else (),
        }
        for kind, identifiers in entities.items():
            for identifier in identifiers:
                previous = first_seen[kind].get(identifier)
                if previous is None or day < previous:
                    first_seen[kind][identifier] = day

    daily_new = {
        kind: Counter(dates.values()) for kind, dates in first_seen.items()
    }
    totals = {
        kind: sum(day < start for day in dates.values())
        for kind, dates in first_seen.items()
    }
    rows = []
    for timestamp in pd.date_range(start, end, freq="D"):
        day = timestamp.date()
        for kind, counts in daily_new.items():
            totals[kind] += counts[day]
        rows.append({
            "Fecha": day.isoformat(),
            "FechaFin": (day + timedelta(days=1)).isoformat(),
            "Escuchas": daily[day],
            **totals,
        })
    return pd.DataFrame(rows)


def growth_period_bounds(period: str, today: date, first: date, last: date) -> tuple[date, date]:
    if period == "Todo":
        return first, max(today, last)
    if period == "Últimos 7 días":
        return today - timedelta(days=6), today
    months = {"Último año": 12, "Últimos 6 meses": 6, "Último mes": 1}[period]
    return (pd.Timestamp(today) - pd.DateOffset(months=months)).date(), today


def render_listening_growth(snapshot: SpotySnapshot) -> None:
    if not snapshot.listening_events:
        return
    st.subheader("Escuchas diarias y evolución acumulada")
    period = st.selectbox(
        "Periodo (UTC)",
        ("Todo", "Último año", "Últimos 6 meses", "Último mes", "Últimos 7 días"),
        key="growth_period",
    )
    dates = [
        datetime.fromtimestamp(event["played_at"], timezone.utc).date()
        for event in snapshot.listening_events
    ]
    start, end = growth_period_bounds(
        period, datetime.now(timezone.utc).date(), min(dates), max(dates),
    )
    if start > end:
        st.info("No hay escuchas en el periodo seleccionado.")
        return
    data = listening_growth(snapshot, start, end)
    if not data["Escuchas"].any():
        st.info("No hay escuchas registradas en este periodo.")
    historical_max = max(Counter(dates).values())
    cumulative = data[["Canciones", "Artistas", "Álbumes"]]
    cumulative_min = int(cumulative.min().min())
    cumulative_max = int(cumulative.max().max())
    axis_max = cumulative_max if cumulative_max > cumulative_min else cumulative_min + 1
    cumulative_ticks = list(range(((cumulative_min + 249) // 250) * 250, axis_max + 1, 250))
    base = alt.Chart(data).encode(
        x=alt.X(
            "Fecha:T", title="Fecha (UTC)", scale=alt.Scale(type="utc"),
            axis=alt.Axis(format="%d/%m/%Y", tickMinStep=86400000),
        ),
    )
    bars = base.mark_bar(color="#94a3b8", binSpacing=0, orient="vertical").encode(
        x2=alt.X2("FechaFin:T"),
        y=alt.Y(
            "Escuchas:Q", title="Escuchas por día",
            scale=alt.Scale(domain=[0, historical_max], nice=False, padding=0),
            axis=alt.Axis(tickMinStep=1, format="d", grid=False),
        ),
        y2=alt.Y2(datum=0),
        tooltip=[alt.Tooltip("Fecha:N", title="Fecha (UTC)"),
                 alt.Tooltip("Escuchas:Q", title="Escuchas", format="d")],
    )
    lines = base.transform_fold(
        ["Canciones", "Artistas", "Álbumes"], as_=["Tipo", "Acumulado"],
    ).mark_line(point=len(data) == 1).encode(
        y=alt.Y(
            "Acumulado:Q", title="Elementos distintos acumulados",
            scale=alt.Scale(domain=[cumulative_min, axis_max], nice=False, zero=False, padding=0),
            axis=alt.Axis(
                values=cumulative_ticks, grid=True,
                labelExpr="datum.value % 1000 === 0 ? format(datum.value, 'd') : ''",
                gridOpacity=alt.ExprRef(expr="datum.value % 1000 === 0 ? 0.5 : 0.15"),
                gridWidth=alt.ExprRef(expr="datum.value % 1000 === 0 ? 1 : 0.5"),
            ),
        ),
        color=alt.Color("Tipo:N", title="Acumulado", scale=alt.Scale(
            domain=["Canciones", "Artistas", "Álbumes"],
            range=["#1DB954", "#2563eb", "#e76f00"],
        )),
        tooltip=[alt.Tooltip("Fecha:N", title="Fecha (UTC)"),
                 alt.Tooltip("Tipo:N", title="Tipo"),
                 alt.Tooltip("Acumulado:Q", title="Total acumulado", format="d")],
    )
    st.altair_chart(
        (bars + lines).resolve_scale(y="independent").properties(height=320),
        width="stretch",
    )


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

    def render_header() -> None:
        st.title("spoty · Resumen de escuchas")
        st.caption(f"Datos de spoty-data · commit `{snapshot.commit[:7]}`")

    def render_metrics() -> None:
        columns = st.columns(4)
        columns[0].metric("Escuchas", f"{len(snapshot.listening_events):,}")
        columns[1].metric("Canciones en catálogo", f"{len(snapshot.tracks):,}")
        columns[2].metric("Álbumes en catálogo", f"{len(snapshot.albums):,}")
        columns[3].metric("Artistas en catálogo", f"{len(snapshot.artists):,}")
        st.caption(f"Ultima escucha registrada: {latest_text}")

    def render_growth() -> None:
        render_listening_growth(snapshot)

    def render_calendar() -> None:
        render_listening_calendar(snapshot)

    top_tracks = plays.most_common(10)

    def render_ranking() -> None:
        if not top_tracks:
            return
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

    def render_recent() -> None:
        recent = sorted(
            snapshot.listening_events,
            key=lambda row: row["played_at"],
            reverse=True,
        )[:RECENT_LISTENING_LIMIT]
        if not recent:
            return
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

    render_layout(
        key="spoty-dashboard",
        horizontal=HORIZONTAL_DASHBOARD,
        vertical=VERTICAL_DASHBOARD,
        regions={
            "metrics": render_metrics,
            "growth": render_growth,
            "calendar": render_calendar,
            "ranking": render_ranking,
            "recent": render_recent,
        },
        header=render_header,
        orientation="auto",
        height="fluid",
    )


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
