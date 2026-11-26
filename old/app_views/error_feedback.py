from typing import Any, MutableMapping

import streamlit as st


INCIDENTS_KEY = "ui_error_incidents"


def _fingerprint(error: Exception) -> tuple[str, str | None, int | None, str | None]:
    return (
        type(error).__name__,
        getattr(error, "action", None),
        getattr(error, "http_status", None),
        getattr(error, "sqlstate", None),
    )


def record_incident(
    state: MutableMapping[str, Any], key: str, error: Exception
) -> dict[str, Any]:
    incidents = state.setdefault(INCIDENTS_KEY, {})
    fingerprint = _fingerprint(error)
    current = incidents.get(key)
    if current and current["fingerprint"] == fingerprint:
        current["repeat_count"] += 1
        return current

    incident = {"fingerprint": fingerprint, "repeat_count": 1}
    incidents[key] = incident
    return incident


def clear_incident(state: MutableMapping[str, Any], key: str) -> None:
    state.get(INCIDENTS_KEY, {}).pop(key, None)


def render_incident_notice(
    key: str,
    error: Exception,
    message: str,
) -> None:
    incident = record_incident(st.session_state, key, error)
    st.warning(message)
    with st.expander("Detalles técnicos"):
        st.write(f"Tipo: {incident['fingerprint'][0]}")
        if incident["fingerprint"][1]:
            st.write(f"Operación: {incident['fingerprint'][1]}")
        if incident["fingerprint"][2] is not None:
            st.write(f"HTTP: {incident['fingerprint'][2]}")
        if incident["fingerprint"][3]:
            st.write(f"SQLSTATE: {incident['fingerprint'][3]}")
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            st.write(f"Reintentar en: {retry_after} s")
        if incident["repeat_count"] > 1:
            st.write(f"Incidentes repetidos: {incident['repeat_count']}")
