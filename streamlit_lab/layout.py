from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Literal

from blocks import Block, Position, Span
from styles import Insets, validate_number


@dataclass(frozen=True)
class Layout:
    rows: int = 3
    columns: int = 1
    flow: Literal["grid", "row", "column"] = "grid"
    horizontal: Literal["start", "center", "end"] = "start"
    vertical: Literal["start", "center", "end"] = "start"
    gap_px: float = 16
    row_gap_px: float | None = None
    column_gap_px: float | None = None
    padding: float | Insets = 0
    column_weights: tuple[float, ...] | None = None
    min_row_height_px: float = 0
    mobile_breakpoint_px: float | None = None

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in (self.rows, self.columns)):
            raise ValueError("El numero de filas y columnas debe ser un entero positivo")
        if self.flow not in ("grid", "row", "column"):
            raise ValueError("Flujo de distribucion no admitido")
        if any(value not in ("start", "center", "end") for value in (self.horizontal, self.vertical)):
            raise ValueError("Alineacion no admitida")
        if not math.isfinite(self.gap_px) or self.gap_px < 0:
            raise ValueError("La separacion debe ser finita y no negativa")
        for value in (self.row_gap_px, self.column_gap_px, self.mobile_breakpoint_px):
            if value is not None:
                validate_number(value, "Separacion o breakpoint")
        validate_number(self.min_row_height_px, "Altura minima de fila")
        if not isinstance(self.padding, Insets):
            validate_number(self.padding, "Padding del layout")
        if self.column_weights is not None:
            if len(self.column_weights) != self.columns:
                raise ValueError("Debe haber un peso por columna")
            for weight in self.column_weights:
                validate_number(weight, "Peso de columna", 0.001)

    @property
    def row_gap(self) -> float:
        return self.gap_px if self.row_gap_px is None else self.row_gap_px

    @property
    def column_gap(self) -> float:
        return self.gap_px if self.column_gap_px is None else self.column_gap_px

    def ordered_blocks(self, blocks: list[Block | Group]) -> list[Block | Group]:
        return [block for block in blocks if block.visible]

    def place(self, blocks: list[Block | Group]) -> dict[str, Position]:
        if len({block.key for block in blocks}) != len(blocks):
            raise ValueError("Las claves de los bloques deben ser unicas")
        blocks = self.ordered_blocks(blocks)
        if self.flow == "grid" and sum(block.span.rows * block.span.columns for block in blocks) > self.rows * self.columns:
            raise ValueError("No hay suficientes celdas para todos los bloques")
        positions = {}
        occupied = set()
        for block in blocks:
            if block.position is not None:
                position = block.position
                if not self._fits(block, position):
                    raise ValueError(f"La posicion de {block.key} queda fuera de la cuadricula")
                cells = block.span.cells(position)
                if cells & occupied:
                    raise ValueError("Dos bloques no pueden ocupar la misma celda")
                positions[block.key] = position
                occupied.update(cells)
        for block in blocks:
            if block.position is None:
                if self.flow == "column":
                    last_column = max(self.columns, max((cell.column for cell in occupied), default=0) + block.span.columns)
                    candidates = (
                        Position(row, column)
                        for column in range(1, last_column - block.span.columns + 2)
                        for row in range(1, self.rows - block.span.rows + 2)
                    )
                else:
                    last_row = max(self.rows, max((cell.row for cell in occupied), default=0) + block.span.rows)
                    row_limit = self.rows if self.flow == "grid" else last_row
                    candidates = (
                        Position(row, column)
                        for row in range(1, row_limit - block.span.rows + 2)
                        for column in range(1, self.columns - block.span.columns + 2)
                    )
                position = next((
                    candidate for candidate in candidates
                    if not block.span.cells(candidate) & occupied
                ), None)
                if position is None:
                    raise ValueError(f"No hay un espacio rectangular libre para {block.key}")
                positions[block.key] = position
                occupied.update(block.span.cells(position))
        return positions

    def _fits(self, block: Block | Group, position: Position) -> bool:
        row_fits = (self.flow != "grid" and self.flow != "column") or position.row + block.span.rows - 1 <= self.rows
        column_fits = (self.flow != "grid" and self.flow != "row") or position.column + block.span.columns - 1 <= self.columns
        return row_fits and column_fits


@dataclass(frozen=True)
class Group:
    key: str
    children: tuple[str | Group, ...]
    layout: Layout
    position: Position | None = None
    span: Span = field(default_factory=Span)
    visible: bool = True

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.key):
            raise ValueError("La clave del grupo debe empezar por una letra y usar a-z, 0-9, _ o -")
        if not isinstance(self.layout, Layout):
            raise ValueError("Cada grupo necesita un Layout")
        if not self.children:
            raise ValueError("Un grupo debe contener al menos un bloque o grupo")
        if any(not isinstance(child, (str, Group)) for child in self.children):
            raise ValueError("Los hijos del grupo deben ser claves de bloque o grupos")
        if any(isinstance(child, str) and not re.fullmatch(r"[a-z][a-z0-9_-]*", child) for child in self.children):
            raise ValueError("Las claves de bloque deben empezar por una letra y usar a-z, 0-9, _ o -")
        if len(set(self.child_keys())) != len(self.child_keys()):
            raise ValueError("Un grupo no puede repetir bloques o grupos")
        if len(set(self.group_keys())) != len(self.group_keys()):
            raise ValueError("Las claves de grupo deben ser unicas")

    def child_keys(self) -> list[str]:
        keys = []
        for child in self.children:
            keys.extend(child.child_keys() if isinstance(child, Group) else [child])
        return keys

    def group_keys(self) -> list[str]:
        keys = [self.key]
        for child in self.children:
            if isinstance(child, Group):
                keys.extend(child.group_keys())
        return keys

    def resolve_children(self, blocks: dict[str, Block]) -> list[Block | Group]:
        resolved = []
        for child in self.children:
            if isinstance(child, Group):
                resolved.append(child)
            elif child in blocks:
                resolved.append(blocks[child])
            else:
                raise ValueError(f"El grupo {self.key} referencia el bloque inexistente {child}")
        return resolved

    def validate(self, blocks: dict[str, Block]) -> None:
        keys = self.child_keys()
        if len(keys) != len(set(keys)):
            raise ValueError("Un bloque no puede pertenecer a mas de un grupo")
        self.layout.place(self.resolve_children(blocks))
        for child in self.children:
            if isinstance(child, Group):
                child.validate(blocks)
