from dataclasses import replace

import streamlit as st

from blocks import Block, Size
from layout import Layout
from styles import BlockStyle, Border, Corners, Insets, Shadow


def edge_controls(label: str, value: float | Insets | Corners, key: str, corners: bool = False):
    with st.expander(label):
        individual = st.checkbox("Valores independientes", value=isinstance(value, (Insets, Corners)), key=f"{key}_individual")
        if not individual:
            initial = value.top_left if isinstance(value, Corners) else value.top if isinstance(value, Insets) else value
            return st.number_input("Todos (px)", min_value=0.0, value=float(initial), key=f"{key}_all")
        names = ("top_left", "top_right", "bottom_right", "bottom_left") if corners else ("top", "right", "bottom", "left")
        labels = ("Superior izquierda", "Superior derecha", "Inferior derecha", "Inferior izquierda") if corners else ("Arriba", "Derecha", "Abajo", "Izquierda")
        values = {
            name: st.number_input(f"{label_text} (px)", min_value=0.0, value=float(getattr(value, name) if isinstance(value, (Insets, Corners)) else value), key=f"{key}_{name}")
            for name, label_text in zip(names, labels)
        }
        return Corners(**values) if corners else Insets(**values)


def layout_controls(layout: Layout) -> Layout:
    """Controles para ajustar la configuracion del layout."""
    flow_labels = {"grid": "Cuadricula fija", "row": "Flujo por filas", "column": "Flujo por columnas"}
    flow = st.selectbox(
        "Distribucion", list(flow_labels),
        index=list(flow_labels).index(layout.flow),
        format_func=flow_labels.get, key="layout_flow",
    )
    rows = st.number_input("Filas", min_value=1, max_value=12, value=layout.rows, key="layout_rows")
    columns = st.number_input("Columnas", min_value=1, max_value=12, value=layout.columns, key="layout_columns")
    horizontal_labels = {"start": "Izquierda", "center": "Centro", "end": "Derecha"}
    vertical_labels = {"start": "Arriba", "center": "Centro", "end": "Abajo"}
    horizontal = st.selectbox(
        "Alineacion horizontal", list(horizontal_labels),
        index=list(horizontal_labels).index(layout.horizontal),
        format_func=horizontal_labels.get, key="layout_horizontal",
    )
    vertical = st.selectbox(
        "Alineacion vertical", list(vertical_labels),
        index=list(vertical_labels).index(layout.vertical),
        format_func=vertical_labels.get, key="layout_vertical",
    )
    gap = st.number_input("Separacion (px)", min_value=0.0, max_value=64.0, value=float(layout.gap_px), key="layout_gap")
    independent = st.checkbox("Separacion por eje", value=layout.row_gap_px is not None or layout.column_gap_px is not None, key="layout_independent_gap")
    row_gap = column_gap = None
    if independent:
        row_gap = st.number_input("Entre filas (px)", min_value=0.0, value=float(layout.row_gap), key="layout_row_gap")
        column_gap = st.number_input("Entre columnas (px)", min_value=0.0, value=float(layout.column_gap), key="layout_column_gap")
    padding = edge_controls("Padding del layout", layout.padding, "layout_padding")
    minimum = st.number_input("Alto minimo de fila (px)", min_value=0.0, value=float(layout.min_row_height_px), key="layout_min_row_height")
    weighted = st.checkbox("Anchos de columna proporcionales", value=layout.column_weights is not None, key="layout_weighted")
    weights = None
    if weighted:
        weights = tuple(
            st.number_input(f"Peso columna {index + 1}", min_value=0.1, value=float(layout.column_weights[index] if layout.column_weights and index < len(layout.column_weights) else 1), key=f"layout_weight_{index}")
            for index in range(columns)
        )
    responsive = st.checkbox("Apilar en pantalla estrecha", value=layout.mobile_breakpoint_px is not None, key="layout_responsive")
    breakpoint = None
    if responsive:
        breakpoint = st.number_input("Ancho limite (px)", min_value=0.0, value=float(layout.mobile_breakpoint_px if layout.mobile_breakpoint_px is not None else 640), key="layout_breakpoint")
    return replace(
        layout, rows=rows, columns=columns, flow=flow, horizontal=horizontal, vertical=vertical,
        gap_px=gap, row_gap_px=row_gap, column_gap_px=column_gap, padding=padding,
        column_weights=weights, min_row_height_px=minimum, mobile_breakpoint_px=breakpoint,
    )


def size_controls(label: str, size: Size, key: str, units: list[str]) -> Size:
    """Controles para ajustar el tamaño (ancho o alto) de un bloque."""
    st.subheader(label)
    unit = st.selectbox("Unidad", units, index=units.index(size.unit), key=f"{key}_unit")
    value = st.number_input("Valor", min_value=0.0, value=float(size.value), key=f"{key}_value")
    minimum = st.number_input("Minimo (px)", min_value=0.0, value=float(size.min_px), key=f"{key}_min")
    limited = st.checkbox("Limitar maximo", value=size.max_px is not None, key=f"{key}_limited")
    maximum = None
    if limited:
        maximum = st.number_input(
            "Maximo (px)", min_value=0.0,
            value=float(size.max_px if size.max_px is not None else max(minimum, 600)),
            key=f"{key}_max",
        )
    return Size(value, unit, minimum, maximum)


def style_controls(style: BlockStyle, key: str) -> BlockStyle:
    padding = edge_controls("Padding del bloque", style.padding, f"{key}_padding")
    radius = edge_controls("Esquinas", style.radius, f"{key}_radius", corners=True)
    with st.expander("Borde"):
        width = edge_controls("Grosor", style.border.width_px, f"{key}_border_width")
        border_styles = ["solid", "dashed", "dotted", "double", "none"]
        border_style = st.selectbox("Estilo", border_styles, index=border_styles.index(style.border.style), key=f"{key}_border_style")
        border_color = st.color_picker("Color del borde", style.border.color, key=f"{key}_border_color")
    with st.expander("Fondo y texto"):
        custom_background = st.checkbox("Sobrescribir fondo del bloque", value=style.background_color is not None, key=f"{key}_custom_background")
        background = st.color_picker("Color de fondo", style.background_color or "#e7f3ee", key=f"{key}_background") if custom_background else None
        opacity = st.slider("Opacidad del fondo", min_value=0.0, max_value=1.0, value=float(style.background_opacity), key=f"{key}_opacity")
        custom_text = st.checkbox("Color de texto manual", value=style.text_color is not None, key=f"{key}_custom_text")
        text = st.color_picker("Color del texto", style.text_color or "#171a19", key=f"{key}_text") if custom_text else None
        image = st.text_input("URL de imagen", value=style.background_image or "", key=f"{key}_image").strip() or None
        fit = st.selectbox("Ajuste de imagen", ["cover", "contain"], index=["cover", "contain"].index(style.background_fit), key=f"{key}_fit")
    with st.expander("Contenido"):
        horizontal_labels = {"start": "Izquierda", "center": "Centro", "end": "Derecha"}
        vertical_labels = {"start": "Arriba", "center": "Centro", "end": "Abajo"}
        horizontal = st.selectbox("Contenido horizontal", list(horizontal_labels), index=list(horizontal_labels).index(style.content_horizontal), format_func=horizontal_labels.get, key=f"{key}_content_horizontal")
        vertical = st.selectbox("Contenido vertical", list(vertical_labels), index=list(vertical_labels).index(style.content_vertical), format_func=vertical_labels.get, key=f"{key}_content_vertical")
        gap = st.number_input("Espacio entre elementos (px)", min_value=0.0, value=float(style.content_gap_px), key=f"{key}_content_gap")
        overflows = ["auto", "hidden", "scroll", "visible"]
        overflow = st.selectbox("Desbordamiento", overflows, index=overflows.index(style.overflow), key=f"{key}_overflow")
    with st.expander("Sombra"):
        enabled = st.checkbox("Activar sombra", value=style.shadow is not None, key=f"{key}_shadow_enabled")
        shadow = None
        if enabled:
            current = style.shadow or Shadow()
            shadow = Shadow(
                x_px=st.number_input("Desplazamiento X (px)", value=float(current.x_px), key=f"{key}_shadow_x"),
                y_px=st.number_input("Desplazamiento Y (px)", value=float(current.y_px), key=f"{key}_shadow_y"),
                blur_px=st.number_input("Desenfoque (px)", min_value=0.0, value=float(current.blur_px), key=f"{key}_shadow_blur"),
                spread_px=st.number_input("Extension (px)", value=float(current.spread_px), key=f"{key}_shadow_spread"),
                color=st.color_picker("Color de sombra", current.color, key=f"{key}_shadow_color"),
                opacity=st.slider("Opacidad de sombra", min_value=0.0, max_value=1.0, value=float(current.opacity), key=f"{key}_shadow_opacity"),
                inset=st.checkbox("Sombra interior", value=current.inset, key=f"{key}_shadow_inset"),
            )
    return replace(
        style, padding=padding, radius=radius, border=Border(width, border_color, border_style),
        shadow=shadow, background_color=background, background_image=image,
        background_fit=fit, background_opacity=opacity, text_color=text,
        content_horizontal=horizontal, content_vertical=vertical, content_gap_px=gap, overflow=overflow,
    )


def block_controls(block: Block) -> Block:
    with st.expander("Comportamiento del bloque"):
        visible = st.checkbox("Visible", value=block.visible, key=f"{block.key}_visible")
        mobile_hidden = st.checkbox("Ocultar en pantalla estrecha", value=block.hide_on_mobile, key=f"{block.key}_mobile_hidden")
        horizontal_labels = {None: "Heredar del layout", "start": "Izquierda", "center": "Centro", "end": "Derecha"}
        vertical_labels = {None: "Heredar del layout", "start": "Arriba", "center": "Centro", "end": "Abajo"}
        horizontal = st.selectbox("Alineacion del bloque horizontal", list(horizontal_labels), index=list(horizontal_labels).index(block.horizontal), format_func=horizontal_labels.get, key=f"{block.key}_horizontal")
        vertical = st.selectbox("Alineacion del bloque vertical", list(vertical_labels), index=list(vertical_labels).index(block.vertical), format_func=vertical_labels.get, key=f"{block.key}_vertical")
        states = ["normal", "selected", "disabled"]
        state = st.selectbox("Estado visual", states, index=states.index(block.state), key=f"{block.key}_state")
    return replace(block, visible=visible, hide_on_mobile=mobile_hidden, horizontal=horizontal, vertical=vertical, state=state)
