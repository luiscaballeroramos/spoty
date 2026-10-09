from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .styles import Alignment

if __package__:
    from .styles import BlockStyle, validate_alignment, validate_number
else:
    from styles import BlockStyle, validate_alignment, validate_number


@dataclass(frozen=True)
class Size:
    """Represents a size with a value, unit, and optional minimum and maximum pixel constraints.
    Units: "px" for pixels, "%" for percentage, "vw" for viewport width, "vh" for viewport height."""
    value: float
    unit: Literal["px", "%", "vw", "vh"] = "px"
    min_px: float = 0
    max_px: float | None = None

    def __post_init__(self):
        if self.unit not in ("px", "%", "vw", "vh"):
            raise ValueError("Unidad de tamano no admitida")
        values = [self.value, self.min_px]
        if self.max_px is not None:
            values.append(self.max_px)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Los tamanos deben ser finitos y no negativos")
        if self.max_px is not None and self.min_px > self.max_px:
            raise ValueError("El minimo no puede superar el maximo")

    def css(self) -> str:
        preferred = f"{self.value:g}{self.unit}"
        if self.max_px is None:
            return f"max({self.min_px:g}px, {preferred})"
        return f"clamp({self.min_px:g}px, {preferred}, {self.max_px:g}px)"


@dataclass(frozen=True)
class Position:
    row: int
    column: int

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in (self.row, self.column)):
            raise ValueError("Fila y columna deben ser enteros positivos")


@dataclass(frozen=True)
class Span:
    rows: int = 1
    columns: int = 1

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in (self.rows, self.columns)):
            raise ValueError("La extension debe tener filas y columnas enteras positivas")

    def cells(self, position: Position) -> set[Position]:
        return {
            Position(row, column)
            for row in range(position.row, position.row + self.rows)
            for column in range(position.column, position.column + self.columns)
        }


@dataclass(frozen=True)
class Block:
    key: str
    color: str = "#e7f3ee"
    width: Size = field(default_factory=lambda: Size(100, "%"))
    height: Size | None = field(default_factory=lambda: Size(100))
    position: Position | None = None
    span: Span = field(default_factory=Span)
    style: BlockStyle = field(default_factory=BlockStyle)
    horizontal: Alignment | None = None
    vertical: Alignment | None = None
    aspect_ratio: float | None = None
    visible: bool = True
    hide_on_mobile: bool = False
    state: Literal["normal", "selected", "disabled"] = "normal"
    hover_style: BlockStyle | None = None
    selected_style: BlockStyle | None = None
    disabled_style: BlockStyle | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.key):
            raise ValueError("La clave debe empezar por una letra y usar a-z, 0-9, _ o -")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("El color debe tener formato #RRGGBB")
        if self.height is not None and self.height.unit == "%":
            raise ValueError("Usa vh para una altura proporcional a la pantalla")
        for alignment in (self.horizontal, self.vertical):
            if alignment is not None:
                validate_alignment(alignment)
        if self.aspect_ratio is not None:
            validate_number(self.aspect_ratio, "Proporcion", 0.001)
            if self.height is not None:
                raise ValueError("Usa height=None para dimensionar el alto con aspect_ratio")
        if self.state not in ("normal", "selected", "disabled"):
            raise ValueError("Estado visual no admitido")

    def active_style(self) -> BlockStyle:
        if self.state == "selected" and self.selected_style is not None:
            return self.selected_style
        if self.state == "disabled" and self.disabled_style is not None:
            return self.disabled_style
        return self.style
