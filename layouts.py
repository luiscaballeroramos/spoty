"""Reusable responsive layouts built from native Streamlit containers."""

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import streamlit as st


def _name(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9-]*", value)) and value != "none"


@dataclass(frozen=True)
class Layout:
    areas: tuple[tuple[str, ...], ...]
    columns: tuple[float, ...]
    rows: tuple[float, ...]
    min_height: int = 180

    def __post_init__(self) -> None:
        if not self.areas or not self.columns or len(self.areas) != len(self.rows):
            raise ValueError("Define una matriz y un peso por fila y columna.")
        if any(len(row) != len(self.columns) for row in self.areas):
            raise ValueError("Todas las filas deben tener el mismo numero de columnas.")
        if any(not math.isfinite(weight) or weight <= 0 for weight in (*self.columns, *self.rows)):
            raise ValueError("Los pesos deben ser positivos y finitos.")
        if type(self.min_height) is not int or self.min_height < 0:
            raise ValueError("La altura minima debe ser un entero no negativo.")
        for name in self.names:
            if not _name(name):
                raise ValueError("Las zonas deben usar letras minusculas, numeros y guiones.")
            cells = [
                (r, c)
                for r, row in enumerate(self.areas)
                for c, area in enumerate(row)
                if area == name
            ]
            row_ids, col_ids = zip(*cells)
            if len(cells) != (
                (max(row_ids) - min(row_ids) + 1)
                * (max(col_ids) - min(col_ids) + 1)
            ):
                raise ValueError(f"La zona {name} debe formar un rectangulo.")

    @property
    def names(self) -> set[str]:
        return {name for row in self.areas for name in row}

    def css(self, screen: bool) -> str:
        columns = " ".join(f"minmax(0, {weight:g}fr)" for weight in self.columns)
        rows = " ".join(
            f"minmax(0, {weight:g}fr)" if screen else "auto"
            for weight in self.rows
        )
        areas = " ".join(f'"{" ".join(row)}"' for row in self.areas)
        return (
            f"grid-template-columns: {columns}; grid-template-rows: {rows}; "
            f"grid-template-areas: {areas}; min-height: {self.min_height}px;"
        )


def render_layout(
    *,
    key: str,
    horizontal: Layout,
    vertical: Layout,
    regions: Mapping[str, Callable[[], None]],
    header: Callable[[], None] | None = None,
    orientation: Literal["auto", "horizontal", "vertical"] = "auto",
    height: Literal["screen", "fluid"] = "screen",
) -> None:
    """Render each region once; CSS alone switches between both layouts."""
    if not _name(key):
        raise ValueError("La clave del layout no es valida.")
    if horizontal.names != vertical.names or set(regions) != horizontal.names:
        raise ValueError("Ambos layouts y el contenido deben definir las mismas zonas.")
    if orientation not in ("auto", "horizontal", "vertical") or height not in ("screen", "fluid"):
        raise ValueError("Orientacion o modo de altura no valido.")
    if any(not callable(render) for render in regions.values()):
        raise ValueError("Cada zona necesita una funcion de renderizado.")
    screen = height == "screen"
    selected = vertical if orientation == "vertical" else horizontal
    shell = f".st-key-{key}-shell"
    grid = f".st-key-{key}-grid"
    wrapper = f'{shell} > [data-testid="stLayoutWrapper"]:has(> {grid})'
    region_selector = ", ".join(f".st-key-{key}-{name}" for name in sorted(regions))
    placements = "".join(
        f"{grid} > div:has(> .st-key-{key}-{name}) {{ grid-area: {name}; }}"
        for name in sorted(regions)
    )
    automatic = (
        f"@media (orientation: portrait) {{ {grid} {{ {vertical.css(screen)} }} "
        f"{wrapper} {{ min-height: {vertical.min_height}px; }} }}"
        if orientation == "auto" else ""
    )
    st.html(f"""
    <style>
    [data-testid="stMainBlockContainer"]:has({shell}) {{
        padding-top: 1rem; padding-bottom: 1rem;
    }}
    {shell} {{
        height: {"calc(100dvh - 5.75rem)" if screen else "auto"};
        min-height: {"600px" if screen else "0"}; gap: .75rem;
    }}
    {wrapper} {{
        flex: {"1 1 0" if screen else "0 0 auto"};
        min-height: {selected.min_height}px;
    }}
    {grid} {{
        display: grid; height: {"100%" if screen else "auto"};
        gap: 12px; {selected.css(screen)}
    }}
    {grid} > [data-testid="stLayoutWrapper"] {{
        min-width: 0; min-height: 0; height: {"100%" if screen else "auto"};
    }}
    {placements}
    {region_selector} {{
        height: {"100%" if screen else "auto"}; min-width: 0; min-height: 0;
        padding: 16px; border: 1px solid #94a3b8; border-radius: 12px;
        box-sizing: border-box; overflow: {"auto" if screen else "visible"};
        overflow-wrap: anywhere;
    }}
    {automatic}
    </style>
    """)
    with st.container(key=f"{key}-shell"):
        if header is not None:
            header()
        with st.container(key=f"{key}-grid"):
            for name, render in regions.items():
                with st.container(key=f"{key}-{name}"):
                    render()
