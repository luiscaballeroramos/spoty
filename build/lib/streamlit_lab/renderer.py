from dataclasses import replace
from collections.abc import Callable
import json
import re

import streamlit as st

from .blocks import Block
from .layout import Group, Layout
from .styles import BlockStyle, ButtonStyle, PageStyle, pixels


__all__ = [
    "render_block",
    "render_button",
    "render_group",
    "render_layout",
    "render_page_style",
]


ALIGNMENTS = {"start": "flex-start", "center": "center", "end": "flex-end"}
_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def render_page_style(style: PageStyle) -> None:
    rules = []
    if style.hide_header:
        rules.append('[data-testid="stHeader"] { display: none !important; }')
    if style.content_padding is not None:
        rules.append(
            f'[data-testid="stMainBlockContainer"] {{ padding: {pixels(style.content_padding)} !important; }}'
        )
    if style.content_overflow is not None:
        rules.append(
            f'[data-testid="stMainBlockContainer"] {{ overflow: {style.content_overflow} !important; }}'
        )
    if style.app_overflow_y is not None:
        rules.append(
            f'[data-testid="stAppViewContainer"] {{ overflow-y: {style.app_overflow_y} !important; }}'
        )
    if style.vertical_gap_px is not None:
        rules.append(
            f'[data-testid="stMainBlockContainer"] > div, '
            f'[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] '
            f'{{ gap: {style.vertical_gap_px:g}px !important; }}'
        )
    if style.horizontal_gap_px is not None:
        rules.append(
            f'[data-testid="stMainBlockContainer"] [data-testid="stHorizontalBlock"] '
            f'{{ row-gap: {style.horizontal_gap_px:g}px !important; '
            f'margin-top: 0 !important; margin-bottom: 0 !important; }}'
        )
    if style.element_margin_px is not None:
        rules.append(
            f'[data-testid="stMainBlockContainer"] [data-testid="stElementContainer"] '
            f'{{ margin-top: {style.element_margin_px:g}px !important; '
            f'margin-bottom: {style.element_margin_px:g}px !important; }}'
        )
    if style.caption_margin_px is not None:
        rules.append(
            f'[data-testid="stCaptionContainer"] '
            f'{{ margin: {pixels(style.caption_margin_px)} !important; }}'
        )
    if rules:
        st.html(f"<style>{''.join(rules)}</style>")


def render_button(
    label: str,
    *,
    key: str,
    style: ButtonStyle | None = None,
    type: str = "secondary",
    help: str | None = None,
    disabled: bool = False,
    use_container_width: bool = True,
    icon: str | None = None,
) -> bool:
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError("La clave debe empezar por una letra y usar letras, numeros, _ o -")

    button_style = style or ButtonStyle()
    declarations = (
        ("background", button_style.background),
        ("background-image", f"url({json.dumps(button_style.background_image)})" if button_style.background_image else None),
        ("background-size", button_style.background_fit if button_style.background_image else None),
        ("background-repeat", "no-repeat" if button_style.background_image else None),
        ("background-position", "center" if button_style.background_image else None),
        ("color", button_style.color),
        ("padding", button_style.padding),
        ("border", button_style.border),
        ("border-radius", button_style.border_radius),
        ("width", button_style.width),
        ("aspect-ratio", button_style.aspect_ratio),
        ("min-height", button_style.min_height),
        ("height", button_style.height),
        ("font-size", button_style.font_size),
        ("font-weight", button_style.font_weight),
        ("text-align", button_style.text_align),
        ("box-shadow", button_style.box_shadow),
        ("max-width", button_style.max_width),
        ("max-height", button_style.max_height),
    )
    css = "\n".join(
        f"{name}: {value};" for name, value in declarations if value is not None
    )
    fill_container_css = ""
    if button_style.fill_container:
        selector = f".st-key-{key}"
        fill_container_css = f"""
        {selector} {{
            width: 100% !important;
            height: 100% !important;
        }}
        {selector} [data-testid="stButton"],
        {selector} [data-testid="stButton"] > div,
        {selector} [data-testid="stTooltipIcon"],
        {selector} [data-testid="stTooltipHoverTarget"] {{
            width: 100% !important;
            height: 100% !important;
        }}
        {selector} [data-testid="stButton"] {{
            display: flex;
            justify-content: center;
        }}
        {selector} [data-testid="stButton"] button {{
            box-sizing: border-box;
            width: 100% !important;
            height: 100% !important;
            max-width: none !important;
            max-height: none !important;
            aspect-ratio: auto !important;
        }}
        """
    portrait_css = ""
    if button_style.portrait is not None:
        portrait_declarations = (
            ("width", button_style.portrait.width),
            ("height", button_style.portrait.height),
            ("min-height", button_style.portrait.min_height),
            ("aspect-ratio", button_style.portrait.aspect_ratio),
        )
        portrait_rules = "\n".join(
            f"{name}: {value} !important;"
            for name, value in portrait_declarations
            if value is not None
        )
        portrait_css = f"""
        @media (orientation: portrait) {{
            .st-key-{key} [data-testid="stButton"] button {{
                {portrait_rules}
            }}
        }}
        """
    st.html(
        f"<style>.st-key-{key} [data-testid=\"stButton\"] button "
        f"{{ box-sizing: border-box; {css} }}"
        f"{fill_container_css}{portrait_css}</style>"
    )
    return st.button(
        label,
        key=key,
        type=type,
        help=help,
        disabled=disabled,
        use_container_width=use_container_width,
        icon=icon,
    )


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
        vertical = ALIGNMENTS[block.vertical or layout.vertical] if isinstance(block, Block) else ALIGNMENTS[layout.vertical]
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
        if isinstance(block, Group):
            cells.append(
                f"""{container_selector} > div > .st-key-{block_key} {{
                    flex: 0 0 auto !important;
                    min-height: 0 !important;
                    width: 100% !important;
                }}"""
            )
    hidden = "\n".join(
        f"{container_selector} > div:has(> .st-key-{'lab-group-' if isinstance(block, Group) else 'lab-'}{block.key}) {{ display: none !important; }}"
        for block in blocks if not block.visible
    )
    mobile = ""
    collapse = f"""{container_selector} {{ grid-template-columns: minmax(0, 1fr) !important; grid-template-rows: none !important; }}
        {container_selector} > div {{ grid-row: auto !important; grid-column: 1 !important; }}"""
    portrait_rows = (
        " ".join(f"minmax(0, {weight:g}fr)" for weight in layout.portrait_row_weights)
        if layout.portrait_row_weights else "none"
    )
    portrait_collapse = f"""{container_selector} {{ grid-template-columns: minmax(0, 1fr) !important; grid-template-rows: {portrait_rows} !important; }}
        {container_selector} > div {{ grid-row: auto !important; grid-column: 1 !important; }}"""
    portrait = (
        f"@media (orientation: portrait) {{ {portrait_collapse} }}"
        if layout.collapse_on_portrait else ""
    )
    if layout.mobile_breakpoint_px is not None:
        mobile_hidden = "\n".join(
            f"{container_selector} > div:has(> .st-key-lab-{block.key}) {{ display: none !important; }}"
            for block in blocks if isinstance(block, Block) and block.hide_on_mobile
        )
        mobile = f"""@media (max-width: {layout.mobile_breakpoint_px:g}px) {{
            {collapse if collapse_on_mobile else ''}
            {mobile_hidden}
        }}"""
    columns = " ".join(f"minmax(0, {weight:g}fr)" for weight in layout.column_weights) if layout.column_weights else f"repeat({layout.columns}, minmax(0, 1fr))"
    parent_gap = ""
    if layout.parent_gap_px is not None:
        parent_gap = f"""[data-testid="stVerticalBlock"]:has(> [data-testid="stLayoutWrapper"] {container_selector}) {{
            gap: {layout.parent_gap_px:g}px !important;
        }}"""
    st.html(
        f"""<style>
        {parent_gap}
        {container_selector} {{
            display: grid !important;
            align-items: stretch !important;
            {f'height: {layout.height.css()} !important; min-height: 0 !important; flex: 0 0 auto !important;' if layout.height is not None else ''}
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
        {portrait}
        </style>"""
    )
    return st.container(key=key, border=False)


def render_block(block: Block):
    hover = ""
    portrait = (
        f"@media (orientation: portrait) {{ .st-key-lab-{block.key} {{ height: {block.portrait_height.css()} !important; }} }}"
        if block.portrait_height is not None else ""
    )
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
        {portrait}
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
