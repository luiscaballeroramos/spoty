"""Listening audio profile and live current-track markers."""

import time
from html import escape

import streamlit as st

from app_views.error_feedback import render_incident_notice
from app_views.reproduction import _get_playback, _get_spotify_client
from app_views.summary import run_registration_with_feedback
from services.pulse import build_pulse
from services.summary_queries import SummaryQueryService
from spotifyapi.streamlit_auth import has_spotify_auth_available


@st.cache_data(ttl=45, show_spinner=False)
def _load_history(current_id: str, hours: int | None, _started_bucket: int, _time_bucket: int):
    return SummaryQueryService().recent_audio_history(
        current_id, _time_bucket * 15, hours, _started_bucket * 15,
    )


def _format_value(value: float | None, key: str) -> str:
    if value is None:
        return "Sin dato"
    if key == "tempo":
        return f"{value:.0f} BPM"
    if key == "loudness":
        return f"{value:.1f} dB"
    return f"{value * 100:.0f}%"


def _heat_color(intensity: float) -> str:
    stops = ((239, 244, 243), (132, 205, 181), (246, 217, 105), (224, 99, 72))
    scaled = min(2.9999, max(0, intensity) * 3)
    index = int(scaled)
    fraction = scaled - index
    channels = [round(start + (end - start) * fraction)
                for start, end in zip(stops[index], stops[index + 1])]
    return f"rgb({channels[0]},{channels[1]},{channels[2]})"


def _render_chart(analysis: dict) -> None:
    rows = []
    for band in analysis["bands"]:
        parameter = band["parameter"]
        cells = "".join(
            f'<span style="background:{_heat_color(value)}"></span>' for value in band["heat"]
        )
        ticks = "".join(
            f'<span class="pulse-tick" style="left:{sample["position"] * 100:.3f}%;'
            f'opacity:{0.12 + 0.88 * sample["weight"]:.3f}" '
            f'title="{escape(sample["name"], quote=True)} · '
            f'{_format_value(sample["value"], parameter.key)} · '
            f'hace {sample["age_minutes"]:.0f} min"></span>'
            for sample in band["samples"]
        )
        marker = ""
        if band["current_position"] is not None:
            marker = (
                f'<span class="pulse-marker" style="left:{band["current_position"] * 100:.3f}%" '
                f'title="Ahora: {_format_value(band["current"], parameter.key)}"></span>'
            )
        count = band.get("sample_count", len(band["samples"]))
        total = analysis["history_count"]
        coverage = 100 * count / total if total else 0
        scale = ""
        if parameter.key == "tempo":
            scale = (
                '<div class="pulse-scale">'
                f'<span>{_format_value(band["minimum"], parameter.key)}</span>'
                f'<span>{_format_value(band["maximum"], parameter.key)}</span></div>'
            )
        rows.append(
            '<div class="pulse-row">'
            f'<div class="pulse-label"><strong>{escape(parameter.label)}</strong>'
            f'<small>({count}/{total} {coverage:.0f}%)</small></div>'
            '<div class="pulse-plot">'
            f'<div class="pulse-lane"><div class="pulse-heat">{cells}</div>{marker}</div>'
            f'<div class="pulse-ticks">{ticks}</div>{scale}</div>'
            f'<strong class="pulse-value">{_format_value(band["current"], parameter.key)}</strong>'
            '</div>'
        )
    st.markdown(
        '<section class="pulse-chart" aria-label="Perfil sonoro reciente">'
        + "".join(rows) + '</section>', unsafe_allow_html=True,
    )


@st.fragment(run_every="15s")
def _render_live_pulse(hours: int | None, expanded: bool) -> None:
    now = time.time()
    playback = {}
    if has_spotify_auth_available():
        try:
            playback = _get_playback(_get_spotify_client()) or {}
        except Exception as error:
            render_incident_notice("pulse_playback_error", error, "No se pudo consultar la canción actual.")
    item = playback.get("item") or {}
    current_id = item.get("id") or ""
    started_at = now - max(0, playback.get("progress_ms") or 0) / 1000 - 5
    try:
        rows = _load_history(current_id, hours, int(started_at // 15), int(now // 15))
    except Exception as error:
        render_incident_notice("pulse_history_error", error, "No se pudo cargar el historial sonoro.")
        return
    analysis = build_pulse(rows, now, hours, expanded)
    images = (item.get("album") or {}).get("images") or []
    image_url = next((image.get("url") for image in images if
                      str(image.get("url", "")).startswith("https://")), None)
    cover = f'<img src="{escape(image_url, quote=True)}" alt="Portada" />' if image_url else ""
    artists = ", ".join(artist.get("name", "") for artist in item.get("artists") or [])
    title = item.get("name") or "Sin reproducción actual"
    status = "Ahora" if playback.get("is_playing") else ("En pausa" if current_id else "Sin conexión en directo")
    st.markdown(
        '<div class="pulse-overview">'
        f'<div class="pulse-total">{analysis["history_count"]} escuchas</div>'
        '<div class="pulse-now">' + cover + '<div class="pulse-now-text">'
        f'<small>{status}</small><h2>{escape(title)}</h2><p>{escape(artists)}</p></div>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    if not analysis["history_count"]:
        st.info("No hay escuchas registradas en el periodo seleccionado.")
    elif not any(band["samples"] for band in analysis["bands"]):
        st.info("Las escuchas del periodo seleccionado aún no tienen parámetros de audio disponibles.")
    _render_chart(analysis)
    st.caption("SmarterPlaylists · Semivida: 30 min · Historial: caché 45 s · Actualización: 15 s")


def render_pulse_page() -> None:
    st.markdown("""
    <style>
    .st-key-pulse_sync button, .st-key-pulse_hours button {
        background:#fff !important; color:#111 !important; border:1px solid #c7cfcb !important;
    }
    .st-key-pulse_hours button[aria-checked="true"] {
        background:#d5eee3 !important; border-color:#448269 !important;
    }
    .st-key-pulse_sync button { min-width:42px; }
    .pulse-overview { display:grid; grid-template-columns:200px minmax(0,1fr); gap:24px; align-items:center; margin:20px 0; }
    .pulse-now { display:flex; gap:18px; align-items:center; min-width:0; }
    .pulse-now img { width:76px; height:76px; object-fit:cover; border-radius:4px; flex-shrink:0; }
    .pulse-now-text { flex:1; min-width:0; }
    .pulse-now h2 { font-size:24px; line-height:1.2; margin:3px 0; padding:0; overflow-wrap:anywhere; }
    .pulse-now p { margin:0; font-size:14px; }
    .pulse-now small, .pulse-label small { display:block; font-size:12px; }
    .pulse-total { text-align:left; font-size:14px; white-space:nowrap; }
    .pulse-chart { width:100%; }
    .pulse-row { display:grid; grid-template-columns:145px minmax(0,1fr) 90px; gap:20px; align-items:start; padding:12px 0 4px; }
    .pulse-label strong { font-size:14px; font-weight:600; }
    .pulse-label small { margin-top:2px; }
    .pulse-lane { position:relative; height:28px; background:#eff4f3; }
    .pulse-heat { display:grid; grid-template-columns:repeat(80,minmax(0,1fr)); height:100%; overflow:hidden; border-radius:3px; }
    .pulse-marker { position:absolute; top:-4px; bottom:-4px; width:4px; background:#111; border:1px solid #fff; transform:translateX(-50%); box-shadow:0 0 0 1px #111; border-radius:2px; }
    .pulse-marker::before { content:""; position:absolute; width:8px; height:8px; background:#111; border:2px solid white; border-radius:50%; top:-7px; left:50%; transform:translateX(-50%); }
    .pulse-ticks { position:relative; height:9px; margin-top:6px; }
    .pulse-tick { position:absolute; top:0; height:7px; width:2px; background:#283c34; transform:translateX(-50%); }
    .pulse-scale { display:flex; justify-content:space-between; font-size:11px; }
    .pulse-value { text-align:right; font-size:16px; font-variant-numeric:tabular-nums; padding-top:2px; }
    @media(max-width:600px) {
        .st-key-pulse_hours button { padding:4px 6px !important; min-width:0; }
        .st-key-pulse_hours button p { font-size:12px; }
        .st-key-pulse_sync button { min-width:36px; padding:4px 6px; }
        .st-key-pulse_expanded label { gap:4px; }
        .st-key-pulse_expanded p { font-size:12px; }
        .pulse-row { grid-template-columns:minmax(0,1fr) 85px; gap:6px 10px; }
        .pulse-label { grid-column:1; display:flex; align-items:baseline; gap:10px; }
        .pulse-label small { margin:0; }
        .pulse-plot { grid-column:1 / -1; grid-row:2; }
        .pulse-value { grid-column:2; grid-row:1; padding-top:0; font-size:14px; }
        .pulse-overview { grid-template-columns:88px minmax(0,1fr); gap:12px; }
        .pulse-now { gap:8px; }
        .pulse-now img { width:48px; height:48px; }
        .pulse-now h2 { font-size:16px; }
        .pulse-now p { font-size:12px; overflow-wrap:anywhere; }
        .pulse-total { font-size:12px; }
    }
    </style>
    """, unsafe_allow_html=True)
    with st.container(horizontal=True, wrap=False, vertical_alignment="center", key="pulse_toolbar"):
        interval = st.segmented_control(
            "Intervalo", ["1h", "6h", "1d", "∞"], default="1d",
            key="pulse_hours", label_visibility="collapsed", wrap=False,
        )
        expanded = st.toggle("All metadata", key="pulse_expanded", wrap=False)
        if st.button(":material/sync:", key="pulse_sync", help="Sincronizar escuchas"):
            run_registration_with_feedback()
            _load_history.clear()
    hours = {"1h": 1, "6h": 6, "1d": 24, "∞": None}.get(interval, 24)
    _render_live_pulse(hours, expanded)
