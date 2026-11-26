import streamlit as st

from services.listening_event_capture import (
    LISTENING_EVENT_SYNC_INTERVAL_SECONDS,
    capture_listening_events_if_due,
)
from services.synchronization import SyncStatus


CAPTURE_RESULT_STATE_KEY = "listening_event_capture_result"


@st.fragment(run_every=LISTENING_EVENT_SYNC_INTERVAL_SECONDS)
def render_listening_event_capture() -> None:
    try:
        result = capture_listening_events_if_due(st.session_state)
    except Exception as exc:
        st.error(
            "No se pudieron guardar las escuchas en el CSV local "
            f"({type(exc).__name__})."
        )
        return

    if result is not None:
        st.session_state[CAPTURE_RESULT_STATE_KEY] = {
            "status": result.status.value,
            "message": result.message,
            "saved": result.counters.get("saved_local_events", 0),
            "duplicates": result.counters.get(
                "skipped_existing_listening_events", 0
            ),
        }

    last_result = st.session_state.get(CAPTURE_RESULT_STATE_KEY)
    if not last_result:
        return

    if last_result["status"] == SyncStatus.COMPLETED.value:
        st.caption(
            "Captura local activa cada 5 minutos. "
            f"Última consulta: {last_result['saved']} nuevas; "
            f"{last_result['duplicates']} repetidas omitidas."
        )
    else:
        message = last_result.get("message") or last_result["status"]
        st.warning(f"No se completó la captura local de escuchas: {message}")
