from dataclasses import replace
from collections.abc import Callable

import streamlit as st

from blocks import Block
from layout import Group, Layout
from styles import BlockStyle, pixels


ALIGNMENTS = {"start": "flex-start", "center": "center", "end": "flex-end"}


def style_css(style: BlockStyle, fallback_color: str) -> str:
    color = style.background_color or fallback_color
    red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
    foreground = style.text_color or ("#171a19" if red * 0.299 + green * 0.587 + blue * 0.114 > 150 else "#ffffff")
    image = f'url("{style.background_image}")' if style.background_image else "none"
    return f"""
        --lab-background: {color};
        --lab-image: {image};
        --lab-background-opacity: {style.background_opacity:g};
        --lab-background-fit: {style.background_fit};
        color: {foreground};
        padding: {pixels(style.padding)};
        border-radius: {pixels(style.radius)};
        border-width: {pixels(style.border.width_px)};
        border-style: {style.border.style};
        border-color: {style.border.color};
        box-shadow: {style.shadow.css() if style.shadow else 'none'};
        overflow: {style.overflow};
        align-items: {ALIGNMENTS[style.content_horizontal]} !important;
        justify-content: {ALIGNMENTS[style.content_vertical]} !important;
        text-align: {dict(start='left', center='center', end='right')[style.content_horizontal]};
        gap: {style.content_gap_px:g}px !important;
    """


def render_layout(
    blocks: list[Block | Group],
    layout: Layout,
    key: str = "lab-layout",
    collapse_on_mobile: bool = True,
):
    positions = layout.place(blocks)
    container_selector = f".st-key-{key}"
    cells = []
    for block in layout.ordered_blocks(blocks):
        block_key = f"lab-group-{block.key}" if isinstance(block, Group) else f"lab-{block.key}"
        horizontal = ALIGNMENTS[block.horizontal or layout.horizontal] if isinstance(block, Block) else "stretch"
        vertical = ALIGNMENTS[block.vertical or layout.vertical] if isinstance(block, Block) else "flex-start"
        cells.append(
            f"""{container_selector} > div:has(> .st-key-{block_key}) {{
            grid-row: {positions[block.key].row} / span {block.span.rows};
            grid-column: {positions[block.key].column} / span {block.span.columns};
            min-width: 0;
            display: flex;
            flex-direction: column;
            align-items: {horizontal};
            justify-content: {vertical};
            width: 100%;
        }}"""
        )
    hidden = "\n".join(
        f"{container_selector} > div:has(> .st-key-{'lab-group-' if isinstance(block, Group) else 'lab-'}{block.key}) {{ display: none !important; }}"
        for block in blocks if not block.visible
    )
    mobile = ""
    if layout.mobile_breakpoint_px is not None:
        mobile_hidden = "\n".join(
            f"{container_selector} > div:has(> .st-key-lab-{block.key}) {{ display: none !important; }}"
            for block in blocks if isinstance(block, Block) and block.hide_on_mobile
        )
        collapse = ""
        if collapse_on_mobile:
            collapse = f"""{container_selector} {{ grid-template-columns: minmax(0, 1fr) !important; grid-template-rows: none !important; }}
            {container_selector} > div {{ grid-row: auto !important; grid-column: 1 !important; }}"""
        mobile = f"""@media (max-width: {layout.mobile_breakpoint_px:g}px) {{
            {collapse}
            {mobile_hidden}
        }}"""
    columns = " ".join(f"minmax(0, {weight:g}fr)" for weight in layout.column_weights) if layout.column_weights else f"repeat({layout.columns}, minmax(0, 1fr))"
    st.html(
        f"""<style>
        {container_selector} {{
            display: grid !important;
            align-items: stretch !important;
            grid-template-columns: {columns};
            grid-template-rows: repeat({layout.rows}, minmax({layout.min_row_height_px:g}px, auto));
            grid-auto-rows: minmax({layout.min_row_height_px:g}px, auto);
            grid-auto-columns: minmax(0, 1fr);
            row-gap: {layout.row_gap:g}px !important;
            column-gap: {layout.column_gap:g}px !important;
            padding: {pixels(layout.padding)};
            box-sizing: border-box;
            min-width: 0;
        }}
        {''.join(cells)}
        {hidden}
        {mobile}
        </style>"""
    )
    return st.container(key=key, border=False)


def render_block(block: Block):
    hover = ""
    if block.hover_style is not None and block.state != "disabled":
        hover = f".st-key-lab-{block.key}:hover {{ {style_css(block.hover_style, block.color)} }}"
    st.html(
        f"""<style>
        .st-key-lab-{block.key} {{
            {style_css(block.active_style(), block.color)}
            position: relative;
            isolation: isolate;
            background: transparent;
            width: {block.width.css()} !important;
            min-width: 0 !important;
            max-width: 100% !important;
            height: {block.height.css() if block.height is not None else 'auto'} !important;
            aspect-ratio: {block.aspect_ratio if block.aspect_ratio is not None else 'auto'};
            min-height: 0 !important;
            flex: 0 0 auto !important;
            box-sizing: border-box;
            opacity: {0.5 if block.state == 'disabled' else 1};
            outline: {'2px solid currentColor' if block.state == 'selected' else 'none'};
            outline-offset: -2px;
            overflow-wrap: anywhere;
        }}
        .st-key-lab-{block.key}::before {{
            content: "";
            position: absolute;
            inset: 0;
            z-index: -1;
            pointer-events: none;
            border-radius: inherit;
            background-color: var(--lab-background);
            background-image: var(--lab-image);
            background-size: var(--lab-background-fit);
            background-position: center;
            background-repeat: no-repeat;
            opacity: var(--lab-background-opacity);
        }}
        .st-key-lab-{block.key} p {{ color: inherit; }}
        {hover}
        </style>"""
    )
    return st.container(key=f"lab-{block.key}", border=False)


def render_group(group: Group, blocks: list[Block], render_content: Callable[[Block], None]):
    if len({block.key for block in blocks}) != len(blocks):
        raise ValueError("Las claves de los bloques deben ser unicas")
    block_map = {block.key: block for block in blocks}
    group.validate(block_map)
    _render_group(group, block_map, render_content, None)


def _render_group(
    group: Group,
    blocks: dict[str, Block],
    render_content: Callable[[Block], None],
    inherited_mobile_breakpoint: float | None,
):
    children = group.resolve_children(blocks)
    mobile_breakpoint = (
        group.layout.mobile_breakpoint_px
        if group.layout.mobile_breakpoint_px is not None
        else inherited_mobile_breakpoint
    )
    layout = group.layout
    if layout.mobile_breakpoint_px is None and mobile_breakpoint is not None:
        layout = replace(layout, mobile_breakpoint_px=mobile_breakpoint)
    with render_layout(
        children,
        layout,
        key=f"lab-group-{group.key}",
        collapse_on_mobile=group.layout.mobile_breakpoint_px is not None,
    ):
        for child in layout.ordered_blocks(children):
            if isinstance(child, Group):
                _render_group(child, blocks, render_content, mobile_breakpoint)
            else:
                with render_block(child):
                    render_content(child)
