import streamlit as st

from app_views.error_feedback import clear_incident, render_incident_notice
from spoty.services.favorites import FavoritesCoordinator
from spoty.services.favorite_recovery import FavoriteRecoveryCallback
from spoty.storage.snapshots import Snapshot


def render_csv_favorite_status(
    snapshot: Snapshot,
    *,
    recover_pending: FavoriteRecoveryCallback | None = None,
) -> None:
    """Show durable favorite intent state; optionally inject explicit recovery."""
    pending = FavoritesCoordinator(
        snapshot.store, snapshot.account, "csv-read-only-status",
    ).pending_change()
    if pending is None:
        st.caption("Sin cambios de favoritos pendientes en el HEAD local actual.")
        return

    action = "marcar como favorito" if pending["desired_liked"] else "quitar de favoritos"
    st.warning(
        f"Hay una operación pendiente para {action} "
        f"(solicitud {pending['request_id']}). No figura como confirmada en el snapshot. "
        "No se repetirá automáticamente; la recuperación requiere confirmar que la "
        "petición anterior terminó y observar el estado remoto."
    )
    if recover_pending is None:
        st.caption("La recuperación todavía no está habilitada en esta interfaz.")
        return

    confirmed = st.checkbox(
        "Confirmo que la petición anterior terminó y ya no está en curso.",
        key=f"csv_favorite_settled_{pending['request_id']}",
    )
    if not st.button(
        "Consultar estado y reconciliar",
        key=f"csv_favorite_recover_{pending['request_id']}",
        disabled=not confirmed,
    ):
        return

    try:
        publication = recover_pending(
            pending["request_id"], remote_settled=confirmed,
        )
    except Exception as error:
        render_incident_notice(
            "csv_favorite_recovery_error",
            error,
            "No se pudo consultar y guardar el estado del favorito. "
            "La operación puede seguir pendiente; no se repetirá la mutación.",
        )
        return

    clear_incident(st.session_state, "csv_favorite_recovery_error")
    if publication is None:
        st.info("La operación ya no está pendiente; no se consultó el estado remoto.")
        return
    st.success(
        "Estado observado y guardado localmente en el inbox de favoritos. "
        "El snapshot consolidado cambiará cuando se procese ese lote."
    )
