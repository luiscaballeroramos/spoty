import os
from urllib.parse import parse_qs, urlparse

import streamlit as st

from config import REDIRECT_URI


def _get_query_param_value(name: str):
    try:
        value = st.query_params.get(name)
    except Exception:
        params = st.experimental_get_query_params()
        value = params.get(name)

    if isinstance(value, list):
        return value[0] if value else None
    return value


def _clear_oauth_query_params() -> None:
    try:
        query_params = st.query_params
        for key in ("code", "state", "error"):
            if key in query_params:
                del query_params[key]
        return
    except Exception:
        pass


def _extract_oauth_code_and_state(raw_callback: str):
    value = (raw_callback or "").strip()
    if not value:
        return None, None

    if value.startswith("http://") or value.startswith("https://"):
        query_text = urlparse(value).query
    elif "?" in value:
        query_text = value.split("?", 1)[1]
    else:
        query_text = value

    parsed = parse_qs(query_text)
    oauth_code = parsed.get("code", [None])[0]
    oauth_state = parsed.get("state", [None])[0]

    if oauth_code:
        return oauth_code, oauth_state

    # Allow pasting only the authorization code.
    return value, None


def complete_spotify_oauth_authorization(oauth_code: str, oauth_state: str = None):
    expected_state = st.session_state.get("spotify_oauth_state")
    if expected_state and oauth_state and oauth_state != expected_state:
        st.session_state["oauth_feedback"] = {
            "type": "error",
            "message": "El estado OAuth no coincide. Pulsa Autorizar Spotify de nuevo.",
        }
        st.session_state.pop("spotify_oauth_state", None)
        return

    try:
        from spotifyapi.spotifyclient import get_oauth_manager

        oauth_manager = get_oauth_manager()
        token_info = oauth_manager.get_access_token(
            code=oauth_code,
            as_dict=True,
            check_cache=False,
        )
        if not token_info:
            raise RuntimeError("Spotify no devolvió un token válido.")
    except Exception as exc:
        st.session_state["oauth_feedback"] = {
            "type": "error",
            "message": f"No se pudo completar la autorización OAuth: {exc}",
        }
    else:
        st.session_state["oauth_feedback"] = {
            "type": "success",
            "message": "Spotify autorizado correctamente. Ya puedes pulsar Actualizar.",
        }
        st.session_state["spotify_oauth_manual_callback"] = ""
    finally:
        st.session_state.pop("spotify_oauth_state", None)

    try:
        current = st.experimental_get_query_params()
        current.pop("code", None)
        current.pop("state", None)
        current.pop("error", None)
        st.experimental_set_query_params(**current)
    except Exception:
        pass


def process_spotify_oauth_callback() -> None:
    oauth_error = _get_query_param_value("error")
    oauth_code = _get_query_param_value("code")
    oauth_state = _get_query_param_value("state")

    if not oauth_error and not oauth_code:
        return

    if oauth_error:
        st.session_state["oauth_feedback"] = {
            "type": "error",
            "message": f"Spotify devolvió un error de autorización: {oauth_error}",
        }
        st.session_state.pop("spotify_oauth_state", None)
        _clear_oauth_query_params()
        return

    complete_spotify_oauth_authorization(oauth_code, oauth_state)
    _clear_oauth_query_params()


def get_spotify_authorize_url():
    try:
        from spotifyapi.spotifyclient import get_oauth_manager

        oauth_manager = get_oauth_manager()
        state = st.session_state.get("spotify_oauth_state")
        if not state:
            state = os.urandom(16).hex()
            st.session_state["spotify_oauth_state"] = state
        return oauth_manager.get_authorize_url(state=state), None
    except Exception as exc:
        return None, str(exc)


def render_oauth_feedback():
    feedback = st.session_state.get("oauth_feedback")
    if not feedback:
        return

    message = feedback.get("message")
    feedback_type = feedback.get("type")

    if not message:
        return

    if feedback_type == "error":
        st.error(message)
    elif feedback_type == "warning":
        st.warning(message)
    else:
        st.success(message)


def render_spotify_authorization_section() -> None:
    authorize_url, auth_error = get_spotify_authorize_url()

    if auth_error:
        st.error(f"No se pudo iniciar OAuth de Spotify: {auth_error}")
        return

    st.markdown(f"[🔐 Autorizar Spotify]({authorize_url})")
    st.caption(
        "Tras autorizar, Spotify te redirige a esta app y se guardará el token en .cache. "
        f"Redirect URI configurado: {REDIRECT_URI}"
    )

    st.caption(
        "Si aparece ERR_CONNECTION_REFUSED en 127.0.0.1, copia la URL final del navegador "
        "(la que contiene ?code=...) y pégala aquí para completar OAuth."
    )

    manual_callback = st.text_input(
        "Callback OAuth (URL completa o solo code)",
        key="spotify_oauth_manual_callback",
        placeholder="http://127.0.0.1:8888/?code=...&state=...",
    )

    if st.button("Completar autorización", key="complete_spotify_oauth"):
        oauth_code, oauth_state = _extract_oauth_code_and_state(manual_callback)
        if not oauth_code:
            st.session_state["oauth_feedback"] = {
                "type": "error",
                "message": "No se encontró el parámetro code en el callback pegado.",
            }
        else:
            complete_spotify_oauth_authorization(oauth_code, oauth_state)


def has_spotify_auth_available() -> bool:
    if os.getenv("SPOTIFY_REFRESH_TOKEN", "").strip():
        return True

    try:
        from spotifyapi.spotifyclient import has_cached_oauth_token
    except Exception:
        return False

    return has_cached_oauth_token()
