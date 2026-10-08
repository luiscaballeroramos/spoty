"""Reusable helpers for composing proportion-based Streamlit page layouts.

Use a keyed Streamlit container as the root of a layout. Apply its shared
spacing rules, create columns with relative width ratios, then render each
column's content inside its own styled block:

    with st.container(key="dashboard-layout"):
        apply_layout_styles("dashboard-layout")
        left, main, right = proportional_columns(
            (1, 2, 1),
            gap=None,
            vertical_alignment="top",
        )

        with left:
            with styled_block(
                "dashboard-left",
                BlockStyle(
                    background="#20242c",
                    padding="1rem",
                    min_height="100vh",
                ),
            ):
                st.write("Left panel")

        with main:
            with styled_block(
                "dashboard-main",
                BlockStyle(background="#11151c", padding="1.5rem"),
            ):
                st.write("Main panel")
                styled_button(
                    "Save changes",
                    key="dashboard-save",
                    style=BlockStyle(
                        background="#1db954",
                        color="#ffffff",
                        border="none",
                        border_radius="0.5rem",
                    ),
                )

The ratio values are relative, not percentages: ``(1, 2, 1)`` creates three
columns where the middle column is twice as wide as either side column.
``apply_layout_styles`` scopes spacing rules to the root container, while
``BlockStyle`` is the single style model for both blocks and buttons. The same
style values are applied to the visual element itself, so shared properties
such as background, border, padding, and dimensions look consistent. Use
unique, valid keys for each root layout, styled block, and button.
"""

import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from numbers import Real
from typing import Iterator, Optional, Sequence

import streamlit as st


_LAYOUT_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_COLUMN_GAPS = {"small", "medium", "large"}
_VERTICAL_ALIGNMENTS = {"top", "center", "bottom"}


@dataclass(frozen=True)
class BlockStyle:
    """Shared CSS properties for :func:`styled_block` and :func:`styled_button`.

    The same properties are applied to the container or the button element,
    allowing both kinds of interface elements to share one visual style.
    Each field accepts a CSS value or ``None`` to leave the property
    unspecified. Values are passed through to CSS as written.

    Attributes:
        background: Background color or background shorthand.
        color: Text and icon color.
        padding: Inner spacing, such as ``"1rem"`` or ``"0.5rem 1rem"``.
        border: Border shorthand, such as ``"1px solid #444"``.
        border_radius: Corner radius, such as ``"0.5rem"``.
        width: Width, such as ``"100%"`` or ``"12rem"``.
        aspect_ratio: Preferred width-to-height ratio, such as ``"1 / 1"``
            for a square element. Avoid setting both a fixed width and fixed
            height when the aspect ratio should determine the final size.
        min_height: Minimum height, such as ``"20rem"`` or ``"100vh"``.
        height: Fixed or viewport-relative height.
        font_size: Text size, such as ``"1rem"``.
        font_weight: Text weight, such as ``"600"`` or ``"bold"``.
        text_align: Text alignment, such as ``"center"``.
        box_shadow: Shadow declaration.
        display: CSS display mode, such as ``"flex"`` or ``"grid"``.
        align_items: Cross-axis alignment when using a flex or grid layout.
        justify_content: Main-axis alignment when using a flex layout.
        overflow: Overflow behavior, such as ``"hidden"`` or ``"auto"``.
        max_width: Maximum width, useful for constraining responsive elements.
        max_height: Maximum height, useful for constraining responsive elements.
    """

    background: Optional[str] = None
    color: Optional[str] = None
    padding: Optional[str] = None
    border: Optional[str] = None
    border_radius: Optional[str] = None
    width: Optional[str] = None
    aspect_ratio: Optional[str] = None
    min_height: Optional[str] = None
    height: Optional[str] = None
    font_size: Optional[str] = None
    font_weight: Optional[str] = None
    text_align: Optional[str] = None
    box_shadow: Optional[str] = None
    display: Optional[str] = None
    align_items: Optional[str] = None
    justify_content: Optional[str] = None
    overflow: Optional[str] = None
    max_width: Optional[str] = None
    max_height: Optional[str] = None


def proportional_columns(
    ratios: Sequence[Real],
    *,
    gap: Optional[str] = None,
    vertical_alignment: str = "top",
):
    """Create Streamlit columns whose widths follow the supplied relative ratios.

    Args:
        ratios: One positive finite number per column. Ratios are relative
            widths; for example, ``(1, 2, 1)`` gives the center column twice
            the width of each side column.
        gap: Streamlit's spacing between columns: ``None``, ``"small"``,
            ``"medium"``, or ``"large"``. Use ``None`` for edge-to-edge columns.
        vertical_alignment: Vertical alignment of the content in each column:
            ``"top"``, ``"center"``, or ``"bottom"``.

    Returns:
        A list of Streamlit column containers in the same order as ``ratios``.

    Raises:
        ValueError: If ratios are empty, non-positive, non-finite, or not
            numeric, or if a spacing/alignment option is unsupported.
    """
    validated_ratios = _validate_ratios(ratios)
    if gap is None:
        gap_kwargs = {"vertical_alignment": vertical_alignment}
    else:
        if gap not in _COLUMN_GAPS:
            raise ValueError("gap must be 'small', 'medium', or 'large'.")
        gap_kwargs = {"gap": gap, "vertical_alignment": vertical_alignment}
    if vertical_alignment not in _VERTICAL_ALIGNMENTS:
        raise ValueError("vertical_alignment must be 'top', 'center', or 'bottom'.")

    return list(st.columns(validated_ratios, **gap_kwargs))


def _validate_ratios(ratios: Sequence[Real]) -> tuple[Real, ...]:
    if not ratios or any(
        isinstance(ratio, bool)
        or not isinstance(ratio, Real)
        or not math.isfinite(ratio)
        or ratio <= 0
        for ratio in ratios
    ):
        raise ValueError("Column ratios must be a non-empty sequence of positive numbers.")
    return tuple(ratios)


def apply_layout_styles(
    layout_key: str,
    *,
    column_gap: str = "0",
    vertical_gap: str = "0",
    column_ratios: Optional[Sequence[Real]] = None,
    portrait_column_ratios: Optional[Sequence[Real]] = None,
    full_block: bool = False,
    full_block_offset: str = "0px",
    center_content: bool = False,
) -> None:
    """Remove Streamlit's default spacing within one keyed layout container.

    This only affects elements inside the container identified by ``layout_key``;
    it does not alter other page sections. Place the call inside the matching
    ``st.container(key=layout_key)`` before rendering the layout's columns.

    Args:
        layout_key: Unique Streamlit key of the root layout container.
        column_gap: CSS value for the horizontal gap between column rows.
            Defaults to ``"0"``.
        vertical_gap: CSS value for the gap between vertically stacked blocks.
            Defaults to ``"0"``.
        column_ratios: Optional relative widths for a grid layout. When set,
            the root's columns preserve these ratios rather than using
            Streamlit's responsive column stacking.
        portrait_column_ratios: Optional relative row heights when the layout
            is stacked in portrait orientation. Must have the same number of
            entries as ``column_ratios``.
        full_block: Whether the layout should fill the viewport height.
        full_block_offset: Height to subtract from the viewport when
            ``full_block`` is enabled, for content rendered after a header.
        center_content: Center each grid column's contents horizontally and
            vertically.

    Raises:
        ValueError: If the key or ratios are invalid, or if portrait and
            landscape ratio counts differ.
    """
    _validate_key(layout_key)
    column_selector = (
        f".st-key-{layout_key} [data-testid=\"stColumn\"], "
        f".st-key-{layout_key} [data-testid=\"column\"], "
        f".st-key-{layout_key} .stColumn"
    )
    columns = (
        _validate_ratios(column_ratios) if column_ratios is not None else None
    )
    portrait_rows = (
        _validate_ratios(portrait_column_ratios)
        if portrait_column_ratios is not None
        else None
    )
    if portrait_rows is not None and (
        columns is None or len(portrait_rows) != len(columns)
    ):
        raise ValueError(
            "portrait_column_ratios must match the number of column_ratios."
        )
    grid_styles = ""
    if columns is not None:
        column_tracks = " ".join(f"minmax(0, {ratio}fr)" for ratio in columns)
        portrait_tracks = (
            " ".join(f"minmax(0, {ratio}fr)" for ratio in portrait_rows)
            if portrait_rows is not None
            else None
        )
        portrait_row_rule = (
            f"grid-template-rows: {portrait_tracks} !important;"
            if portrait_tracks
            else ""
        )
        grid_styles = f"""
        .st-key-{layout_key} [data-testid="stHorizontalBlock"] {{
            display: grid !important;
            grid-template-columns: {column_tracks} !important;
            grid-template-rows: minmax(0, 1fr) !important;
            align-items: stretch !important;
        }}
        {column_selector} {{
            width: 100% !important;
            min-width: 0 !important;
            box-sizing: border-box;
        }}
        @media (orientation: portrait) {{
            .st-key-{layout_key} [data-testid="stHorizontalBlock"] {{
                grid-template-columns: minmax(0, 1fr) !important;
                {portrait_row_rule}
            }}
        }}
        """
    full_block_styles = (
        f"""
        .st-key-{layout_key} {{
            box-sizing: border-box;
            height: calc(100dvh - {full_block_offset}) !important;
            min-height: calc(100dvh - {full_block_offset}) !important;
            overflow: hidden;
        }}
        .st-key-{layout_key} [data-testid="stHorizontalBlock"] {{
            height: 100% !important;
        }}
        .st-key-{layout_key} > [data-testid="stLayoutWrapper"] {{
            height: 100% !important;
            flex: 1 1 0% !important;
            min-height: 0 !important;
        }}
        {column_selector} {{
            height: 100% !important;
            min-height: 0 !important;
        }}
        {column_selector} > [data-testid="stVerticalBlock"] {{
            height: 100% !important;
            min-height: 0 !important;
        }}
        """
        if full_block
        else ""
    )
    centered_content_styles = (
        f"""
        .st-key-{layout_key} [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
        .st-key-{layout_key} [data-testid="column"] > [data-testid="stVerticalBlock"],
        .st-key-{layout_key} .stColumn > [data-testid="stVerticalBlock"] {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
        }}
        """
        if center_content
        else ""
    )
    st.markdown(
        f"""
        <style>
        .st-key-{layout_key} [data-testid="stHorizontalBlock"] {{
            column-gap: {column_gap} !important;
            row-gap: {vertical_gap} !important;
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }}
        .st-key-{layout_key} [data-testid="stVerticalBlock"] {{
            gap: {vertical_gap} !important;
        }}
        .st-key-{layout_key} [data-testid="stElementContainer"] {{
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }}
        {column_selector} {{
            min-width: 0 !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
        }}
        {grid_styles}
        {full_block_styles}
        {centered_content_styles}
        </style>
        """,
        unsafe_allow_html=True,
    )


@contextmanager
def styled_block(key: str, style: Optional[BlockStyle] = None) -> Iterator[None]:
    """Render a Streamlit container with independent, key-scoped CSS styling.

    Use this as a context manager around one block's content. Its CSS selector
    is derived from ``key`` and is independent of the root layout and sibling
    blocks. Only the properties set in ``style`` are emitted.

    Args:
        key: Unique key for this styled Streamlit container.
        style: CSS properties for the block. If omitted, the container is
            rendered without additional visual properties.

    Yields:
        None. The context manager yields control to the block's content.

    Raises:
        ValueError: If ``key`` does not follow the supported key format.
    """
    _validate_key(key)
    css = _style_css(style or BlockStyle())
    st.markdown(
        f"<style>.st-key-{key} {{ box-sizing: border-box; {css} }}</style>",
        unsafe_allow_html=True,
    )
    with st.container(key=key):
        yield


def styled_button(
    label: str,
    *,
    key: str,
    style: Optional[BlockStyle] = None,
    type: str = "secondary",
    help: Optional[str] = None,
    disabled: bool = False,
    use_container_width: bool = True,
    icon: Optional[str] = None,
) -> bool:
    """Render a Streamlit button with independently configurable CSS styling.

    Uses the same :class:`BlockStyle` as :func:`styled_block`. The style
    declarations target the button element itself and are shared in structure
    with the declarations used for styled containers.

    Args:
        label: Text displayed inside the button.
        key: Unique Streamlit key and CSS selector scope for this button.
        style: Shared visual properties for the button or a styled block.
        type: Streamlit button variant, such as ``"primary"``,
            ``"secondary"``, or ``"tertiary"``.
        help: Optional tooltip text shown when hovering over the button.
        disabled: Whether the button is disabled.
        use_container_width: Whether Streamlit expands the button to its
            container width.
        icon: Optional Streamlit icon name or emoji.

    Returns:
        ``True`` when the button is clicked during the current run, otherwise
        ``False``.

    Raises:
        ValueError: If ``key`` does not follow the supported key format.
    """
    _validate_key(key)
    css = _style_css(style or BlockStyle())
    st.markdown(
        f"""
        <style>
        .st-key-{key} [data-testid="stButton"] button {{
            box-sizing: border-box; {css}
        }}
        </style>
        """,
        unsafe_allow_html=True,
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


def _style_css(style: BlockStyle) -> str:
    declarations = (
        ("background", style.background),
        ("color", style.color),
        ("padding", style.padding),
        ("border", style.border),
        ("border-radius", style.border_radius),
        ("width", style.width),
        ("aspect-ratio", style.aspect_ratio),
        ("min-height", style.min_height),
        ("height", style.height),
        ("font-size", style.font_size),
        ("font-weight", style.font_weight),
        ("text-align", style.text_align),
        ("box-shadow", style.box_shadow),
        ("display", style.display),
        ("align-items", style.align_items),
        ("justify-content", style.justify_content),
        ("overflow", style.overflow),
        ("max-width", style.max_width),
        ("max-height", style.max_height),
    )
    return "\n".join(
        f"{property_name}: {value};"
        for property_name, value in declarations
        if value is not None
    )


def _validate_key(key: str) -> None:
    if not _LAYOUT_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            "Layout keys must start with a letter and contain only letters, "
            "numbers, underscores, or hyphens."
        )
