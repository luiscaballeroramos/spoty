import streamlit as st

from streamlit_lab.blocks import Block
from streamlit_lab.layout import Group, Layout
from streamlit_lab.renderer import render_group
from streamlit_lab.styles import BlockStyle


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
)
NAVIGATION_GROUP = Group(
    "top-navigation",
    ("summary", "reproduction"),
    Layout(rows=1, columns=2, gap_px=8, padding=0, parent_gap_px=0),
)


def render_top_navigation(
    current_page: str,
    on_summary_click,
    on_reproduction_click,
):

    def render_navigation_block(block: Block):
        if block.key == "summary":
            label = "📊"
            key = "top_nav_summary"
            on_click = on_summary_click
        else:
            label = "⏯️"
            key = "top_nav_reproduction"
            on_click = on_reproduction_click

        if st.button(label, key=key, type="secondary", use_container_width=True):
            on_click()

    render_group(
        NAVIGATION_GROUP,
        list(NAVIGATION_BLOCKS),
        render_navigation_block,
    )

