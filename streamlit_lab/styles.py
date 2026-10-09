from dataclasses import dataclass, field
import math
import re
from typing import Literal
from urllib.parse import urlsplit


Alignment = Literal["start", "center", "end"]


def validate_number(value: float, label: str, minimum: float = 0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label}: se requiere un numero finito mayor o igual a {minimum:g}")


def validate_color(value: str):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("El color debe tener formato #RRGGBB")


def validate_alignment(value: str):
    if value not in ("start", "center", "end"):
        raise ValueError("Alineacion no admitida")


@dataclass(frozen=True)
class Insets:
    top: float = 0
    right: float = 0
    bottom: float = 0
    left: float = 0

    def __post_init__(self):
        for value in (self.top, self.right, self.bottom, self.left):
            validate_number(value, "Espacio")

    def css(self) -> str:
        return " ".join(f"{value:g}px" for value in (self.top, self.right, self.bottom, self.left))


@dataclass(frozen=True)
class Corners:
    top_left: float = 0
    top_right: float = 0
    bottom_right: float = 0
    bottom_left: float = 0

    def __post_init__(self):
        for value in (self.top_left, self.top_right, self.bottom_right, self.bottom_left):
            validate_number(value, "Radio")

    def css(self) -> str:
        return " ".join(f"{value:g}px" for value in (self.top_left, self.top_right, self.bottom_right, self.bottom_left))


def pixels(value: float | Insets | Corners) -> str:
    return value.css() if isinstance(value, (Insets, Corners)) else f"{value:g}px"


@dataclass(frozen=True)
class Border:
    width_px: float | Insets = 0
    color: str = "#36413b"
    style: Literal["solid", "dashed", "dotted", "double", "none"] = "solid"

    def __post_init__(self):
        if not isinstance(self.width_px, Insets):
            validate_number(self.width_px, "Grosor")
        validate_color(self.color)
        if self.style not in ("solid", "dashed", "dotted", "double", "none"):
            raise ValueError("Estilo de borde no admitido")


@dataclass(frozen=True)
class Shadow:
    x_px: float = 0
    y_px: float = 2
    blur_px: float = 8
    spread_px: float = 0
    color: str = "#000000"
    opacity: float = 0.15
    inset: bool = False

    def __post_init__(self):
        for value in (self.x_px, self.y_px, self.spread_px):
            validate_number(value, "Sombra", -1e6)
        validate_number(self.blur_px, "Desenfoque")
        validate_number(self.opacity, "Opacidad")
        if self.opacity > 1:
            raise ValueError("La opacidad debe estar entre 0 y 1")
        validate_color(self.color)

    def css(self) -> str:
        channels = ", ".join(str(int(self.color[index:index + 2], 16)) for index in (1, 3, 5))
        return f"{'inset ' if self.inset else ''}{self.x_px:g}px {self.y_px:g}px {self.blur_px:g}px {self.spread_px:g}px rgba({channels}, {self.opacity:g})"


@dataclass(frozen=True)
class BlockStyle:
    padding: float | Insets = 20
    radius: float | Corners = 6
    border: Border = field(default_factory=Border)
    shadow: Shadow | None = None
    background_color: str | None = None
    background_image: str | None = None
    background_fit: Literal["cover", "contain"] = "cover"
    background_opacity: float = 1
    text_color: str | None = None
    content_horizontal: Alignment = "start"
    content_vertical: Alignment = "start"
    content_gap_px: float = 16
    overflow: Literal["auto", "hidden", "scroll", "visible"] = "auto"

    def __post_init__(self):
        if not isinstance(self.padding, Insets):
            validate_number(self.padding, "Padding")
        if not isinstance(self.radius, Corners):
            validate_number(self.radius, "Radio")
        for color in (self.background_color, self.text_color):
            if color is not None:
                validate_color(color)
        validate_number(self.background_opacity, "Opacidad")
        if self.background_opacity > 1:
            raise ValueError("La opacidad debe estar entre 0 y 1")
        if self.background_image is not None:
            url = urlsplit(self.background_image)
            if url.scheme not in ("http", "https") or not url.netloc or re.search(r"[\s<>\"'\\()]", self.background_image):
                raise ValueError("La imagen debe ser una URL HTTP(S) sin caracteres especiales sin codificar")
        if self.background_fit not in ("cover", "contain"):
            raise ValueError("Ajuste de imagen no admitido")
        validate_alignment(self.content_horizontal)
        validate_alignment(self.content_vertical)
        validate_number(self.content_gap_px, "Separacion del contenido")
        if self.overflow not in ("auto", "hidden", "scroll", "visible"):
            raise ValueError("Desbordamiento no admitido")
