import streamlit as st

from streamlit_lab import Block, BlockStyle, Group, Layout
from streamlit_lab.renderer import render_group


NAVIGATION_STYLE = BlockStyle(
    padding=0,
    radius=0,
    background_opacity=0,
    content_gap_px=0,
    overflow="visible",
)
NAVIGATION_BLOCKS = (
    Block("summary", height=None, style=NAVIGATION_STYLE),
    Block("reproduction", height=None, style=NAVIGATION_STYLE),
    Block("metadata", height=None, style=NAVIGATION_STYLE),
)
NAVIGATION_GROUP = Group(
    "top-navigation",
    ("summary", "reproduction", "metadata"),
    Layout(rows=1, columns=3, gap_px=8, padding=0, parent_gap_px=0),
)


def render_top_navigation(
    current_page: str,
    on_summary_click,
    on_reproduction_click,
    on_metadata_click,
):
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"] {
            padding-top: 0 !important;
        }
        .st-key-lab-group-top-navigation {
            position: sticky;
            top: 0;
            z-index: 1000;
            background: #ffffff;
        }
        .st-key-lab-group-top-navigation button {
            background: #ffffff !important;
            color: #111111 !important;
            border: 1px solid #111111 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    def render_navigation_block(block: Block):
        if block.key == "summary":
            label = ":material/bar_chart:"
            key = "top_nav_summary"
            on_click = on_summary_click
        elif block.key == "reproduction":
            label = ":material/headphones:"
            key = "top_nav_reproduction"
            on_click = on_reproduction_click
        else:
            label = ":material/database:"
            key = "top_nav_metadata"
            on_click = on_metadata_click

        if st.button(
            label, key=key, type="secondary", use_container_width=True,
            help={"summary": "Resumen", "reproduction": "Reproduccion", "metadata": "Metadatos"}[block.key],
        ):
            on_click()

    render_group(
        NAVIGATION_GROUP,
        list(NAVIGATION_BLOCKS),
        render_navigation_block,
    )

