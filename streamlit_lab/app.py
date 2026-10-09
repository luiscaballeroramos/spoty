from dataclasses import asdict, replace
import json

import streamlit as st

from blocks import Block, Position, Size, Span
from controls import block_controls, layout_controls, size_controls, style_controls
from layout import Group, Layout
from renderer import render_group


st.set_page_config(page_title="Laboratorio de bloques", page_icon=":material/dashboard:", layout="wide")

DEFAULT_BLOCKS = [
        Block("principal", "#fc0808", height=Size(160), position=Position(1, 1), span=Span(2, 2)),
        Block("secundario", "#f5df83", height=Size(160), position=Position(1, 1), span=Span(1, 2)),
        Block("detalle", "#0378ff", position=Position(3, 1)),
        Block("actividad", "#cce8df", position=Position(3, 2)),
        Block("resumen", "#e7cddd", height=Size(100), position=Position(3, 1), span=Span(1, 2)),
]
DEFAULT_GROUP = Group(
    "dashboard",
    children=(
            Group("contenido", ("principal", "resumen"),
                  Layout(rows=3, columns=2, horizontal='center', vertical='start')),
            Group("lateral", ("secundario", "detalle", "actividad"),
                  Layout(rows=3, columns=2, horizontal='center', vertical='start')),
    ),
    layout=Layout(rows=1, columns=2, gap_px=16, mobile_breakpoint_px=640),
)


def reset_example():
    prefixes = ("layout_", *(f"{block.key}_" for block in DEFAULT_BLOCKS))
    for key in list(st.session_state):
        if key.startswith(prefixes) or key == "selected_block":
            del st.session_state[key]
    st.session_state.blocks = list(DEFAULT_BLOCKS)
    st.session_state.group = DEFAULT_GROUP

if "group" not in st.session_state or not isinstance(st.session_state.group, Group):
    reset_example()
elif "blocks" not in st.session_state:
    st.session_state.blocks = list(DEFAULT_BLOCKS)


with st.sidebar:
    st.title("Bloques")
    st.button("Restablecer ejemplo", icon=":material/restart_alt:", on_click=reset_example, key="reset_example")
    with st.expander("Distribucion"):
        try:
            candidate_layout = layout_controls(st.session_state.group.layout)
            candidate_group = replace(st.session_state.group, layout=candidate_layout)
            candidate_group.validate({block.key: block for block in st.session_state.blocks})
            st.session_state.group = candidate_group
        except ValueError as error:
            st.error(str(error))
    block_keys = [block.key for block in st.session_state.blocks]
    selected = st.selectbox(
        "Bloque", range(len(block_keys)),
        format_func=block_keys.__getitem__, key="selected_block",
    )
    current = st.session_state.blocks[selected]
    color = st.color_picker("Fondo", current.color, key=f"{current.key}_color")
    try:
        candidate = block_controls(current)
        with st.expander("Apariencia del bloque"):
            style = style_controls(current.style, f"{current.key}_style")
        width = size_controls("Ancho", current.width, f"{current.key}_width", ["%", "vw", "px"])
        auto_height = st.checkbox("Alto automatico", value=current.height is None, key=f"{current.key}_auto_height")
        ratio = None
        if auto_height:
            height = None
            use_ratio = st.checkbox("Fijar proporcion ancho/alto", value=current.aspect_ratio is not None, key=f"{current.key}_use_ratio")
            if use_ratio:
                ratio = st.number_input("Proporcion", min_value=0.001, value=float(current.aspect_ratio or 1), key=f"{current.key}_ratio")
        else:
            height = size_controls("Alto", current.height or Size(180), f"{current.key}_height", ["vh", "px"])
        candidate_blocks = list(st.session_state.blocks)
        candidate_blocks[selected] = replace(candidate, color=color, width=width, height=height, style=style, aspect_ratio=ratio)
        st.session_state.group.validate({block.key: block for block in candidate_blocks})
        st.session_state.blocks = candidate_blocks
    except ValueError as error:
        st.error(str(error))

st.title("Laboratorio de bloques")


def render_block_content(block):
    st.markdown(f"**{block.key.capitalize()}**")


render_group(st.session_state.group, st.session_state.blocks, render_block_content)

with st.expander("Configuracion"):
    configuration = {
        "group": asdict(st.session_state.group),
        "blocks": [asdict(block) for block in st.session_state.blocks],
    }
    st.json(configuration)
    st.download_button(
        "Descargar JSON", json.dumps(configuration, indent=2),
        file_name="blocks.json", mime="application/json", icon=":material/download:",
    )
