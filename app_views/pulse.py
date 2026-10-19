"""Listening audio profile and live current-track markers."""

import os
import queue
import threading
import time
from html import escape
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from app_views.error_feedback import render_incident_notice
from app_views.reproduction import _get_playback, _get_spotify_client
from app_views.summary import run_registration_with_feedback
from services.pulse import EXTRA_PARAMETERS, PARAMETERS, audio_value, build_pulse
from services.summary_queries import SummaryQueryService
from spotifyapi.streamlit_auth import has_spotify_auth_available


_CURRENT_TRACK_ENRICHMENT_KEY = "pulse_current_track_enrichment_v2"
_CURRENT_TRACK_AUDIO_KEY = "pulse_current_track_audio"
_CURRENT_TRACK_LOOKUP_STATUS_KEY = "pulse_current_track_lookup_status"


@st.cache_data(ttl=45, show_spinner=False)
def _load_history(current_id: str, hours: int | None, _started_bucket: int, _time_bucket: int):
    return SummaryQueryService().recent_audio_history(
        current_id, _time_bucket * 15, hours, _started_bucket * 15,
    )


def _refresh_current_track_metadata(track_id: str, result_queue) -> None:
    try:
        from metadata_sources.smarterplaylists import (
            SmarterPlaylistsAdapter,
            load_session_cookie,
        )
        from config import require_database_url
        from services.smarterplaylists_refresh import SmarterPlaylistsRefreshService

        cookie, session_file = _smarterplaylists_session()
        cookie = load_session_cookie(value=cookie, path=session_file)
        service = SmarterPlaylistsRefreshService(
            require_database_url(),
            lambda: SmarterPlaylistsAdapter(cookie),
        )
        result_queue.put(("result", service.lookup_track(track_id, persist=True)))
    except Exception as error:
        result_queue.put(("error", error))


def _smarterplaylists_session() -> tuple[str | None, str | None]:
    configured_file = os.getenv("SMARTERPLAYLISTS_SESSION_FILE", "").strip()
    if configured_file:
        return None, configured_file

    cookie = os.getenv("SMARTERPLAYLISTS_SESSION_COOKIE", "").strip()
    if cookie:
        return cookie, None

    configured_file = _streamlit_secret("SMARTERPLAYLISTS_SESSION_FILE")
    if configured_file:
        return None, str(configured_file).strip()

    cookie = _streamlit_secret("SMARTERPLAYLISTS_SESSION_COOKIE")
    if cookie:
        return str(cookie).strip(), None

    default_file = Path(__file__).resolve().parents[1] / ".secrets" / "smarterplaylists-session.json"
    return (None, str(default_file)) if default_file.is_file() else (None, None)


def _streamlit_secret(key: str):
    try:
        value = st.secrets.get(key)
        if value:
            return value
        section = st.secrets.get("smarterplaylists", {})
        return section.get(key.removeprefix("SMARTERPLAYLISTS_").lower())
    except StreamlitSecretNotFoundError:
        return None


def _start_current_track_enrichment(track_id: str, audio: dict, state) -> bool:
    if (
        not track_id
        or _has_all_primary_audio(audio)
        or state.get(_CURRENT_TRACK_ENRICHMENT_KEY) == track_id
    ):
        return False

    cookie, session_file = _smarterplaylists_session()
    if not cookie and not session_file:
        return False

    state[_CURRENT_TRACK_ENRICHMENT_KEY] = track_id
    result_queue = queue.Queue(maxsize=1)
    threading.Thread(
        target=_refresh_current_track_metadata,
        args=(track_id, result_queue),
        daemon=True,
        name="pulse-track-metadata",
    ).start()
    state[f"{_CURRENT_TRACK_ENRICHMENT_KEY}_pending"] = (track_id, result_queue)
    return True


def _has_current_audio(audio: dict) -> bool:
    return any(
        audio_value(audio, parameter) is not None
        for parameter in PARAMETERS + EXTRA_PARAMETERS
    )


def _has_all_primary_audio(audio: dict) -> bool:
    return all(audio_value(audio, parameter) is not None for parameter in PARAMETERS)


def _collect_current_track_enrichment(track_id: str, state) -> None:
    pending = state.get(f"{_CURRENT_TRACK_ENRICHMENT_KEY}_pending")
    if not pending or pending[0] != track_id:
        return
    try:
        result_type, result = pending[1].get_nowait()
    except queue.Empty:
        return

    state.pop(f"{_CURRENT_TRACK_ENRICHMENT_KEY}_pending", None)
    if result_type == "error":
        render_incident_notice(
            "pulse_current_track_metadata_error",
            result,
            "No se pudieron consultar los parámetros de audio de la canción actual.",
        )
    elif result.get("status") == "completed" and isinstance(result.get("audio"), dict):
        state[_CURRENT_TRACK_AUDIO_KEY] = (track_id, result["audio"])
        _load_history.clear()
        state[_CURRENT_TRACK_LOOKUP_STATUS_KEY] = (
            track_id,
            "completed" if _has_current_audio(result["audio"]) else "no_audio",
        )
    elif result.get("status") == "not_found":
        state[_CURRENT_TRACK_LOOKUP_STATUS_KEY] = (track_id, "not_found")
    elif result.get("status") != "not_found":
        error = RuntimeError(
            "La consulta de metadatos de la canción actual no se completó."
        )
        error.action = "refresh current track metadata"
        render_incident_notice(
            "pulse_current_track_metadata_error",
            error,
            "No se pudieron consultar los parámetros de audio de la canción actual.",
        )


def _attach_current_track_audio(rows: list[dict], track_id: str, state) -> dict:
    current_row = next((row for row in rows if row.get("kind") == "current"), None)
    current_audio = (current_row.get("audio") or {}) if current_row else {}
    cached_audio = state.get(_CURRENT_TRACK_AUDIO_KEY)
    if cached_audio and cached_audio[0] == track_id:
        current_audio = {**cached_audio[1], **current_audio}
        if current_row:
            current_row["audio"] = current_audio
        elif track_id:
            rows.append({"kind": "current", "audio": current_audio})
    return current_audio


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
    _collect_current_track_enrichment(current_id, st.session_state)
    started_at = now - max(0, playback.get("progress_ms") or 0) / 1000 - 5
    try:
        rows = _load_history(current_id, hours, int(started_at // 15), int(now // 15))
    except Exception as error:
        render_incident_notice("pulse_history_error", error, "No se pudo cargar el historial sonoro.")
        return
    current_audio = _attach_current_track_audio(rows, current_id, st.session_state)
    smarterplaylists_configured = any(_smarterplaylists_session())
    enrichment_started = _start_current_track_enrichment(
        current_id, current_audio, st.session_state,
    )
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
    if current_id and not _has_current_audio(current_audio) and not smarterplaylists_configured:
        st.caption("SmarterPlaylists no está configurado; no se ha podido consultar esta canción.")
    if enrichment_started or (
        st.session_state.get(f"{_CURRENT_TRACK_ENRICHMENT_KEY}_pending", (None,))[0]
        == current_id
    ):
        st.caption("Consultando una vez los parámetros de audio de esta canción…")
    lookup_status = st.session_state.get(_CURRENT_TRACK_LOOKUP_STATUS_KEY)
    if current_id and lookup_status and lookup_status[0] == current_id:
        if lookup_status[1] == "not_found":
            st.caption("SmarterPlaylists no devolvió datos para esta canción.")
        elif lookup_status[1] == "no_audio":
            st.caption("SmarterPlaylists respondió, pero no incluyó parámetros de audio.")
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
    .st-key-pulse_expanded label > span:has(> input[role="switch"]) + div {
        background:#fff !important; border:1px solid #111 !important;
    }
    .st-key-pulse_expanded label > span:has(> input[role="switch"]) + div > div {
        background:#111 !important;
    }
    .st-key-pulse_expanded label:has(> span > input[role="switch"]:checked) > span + div {
        background:#888 !important; border-color:#666 !important;
    }
    .st-key-pulse_expanded label:has(> span > input[role="switch"]:checked) > span + div > div {
        background:#fff !important;
    }
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
        if st.button(":material/sync:", key="pulse_sync"):
            run_registration_with_feedback()
            _load_history.clear()
    hours = {"1h": 1, "6h": 6, "1d": 24, "∞": None}.get(interval, 24)
    _render_live_pulse(hours, expanded)
