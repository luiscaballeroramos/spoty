from dataclasses import dataclass, field
import math
import re
from typing import Literal
from urllib.parse import urlsplit


Alignment = Literal["start", "center", "end"]

_CSS_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9#%(),./ _+*-]+$")
_CSS_LENGTH_PATTERN = re.compile(
    r"^(?:0|(?:\d+(?:\.\d*)?|\.\d+)(?:px|%|em|rem|vw|vh|dvh|svh|lvh|vmin|vmax|ch|ex|cm|mm|in|pt|pc)|"
    r"(?:auto|min-content|max-content|fit-content|stretch|available)|"
    r"(?:calc|min|max|clamp|var|fit-content)\([A-Za-z0-9#%(),./ _+*-]+\))$",
    re.IGNORECASE,
)
_CSS_NUMBER_PATTERN = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")
_FONT_SIZE_KEYWORDS = {
    "xx-small", "x-small", "small", "medium", "large", "x-large",
    "xx-large", "xxx-large", "smaller", "larger",
}


def validate_number(value: float, label: str, minimum: float = 0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label}: se requiere un numero finito mayor o igual a {minimum:g}")


def validate_color(value: str):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("El color debe tener formato #RRGGBB")


def validate_alignment(value: str):
    if value not in ("start", "center", "end"):
        raise ValueError("Alineacion no admitida")


def _validate_css_value(value: str | None, label: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _CSS_VALUE_PATTERN.fullmatch(value)
        or "/*" in value
        or "*/" in value
        or re.search(r"(?:url|expression)\s*\(", value, re.IGNORECASE)
    ):
        raise ValueError(f"{label}: valor CSS no admitido")

    depth = 0
    for character in value:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"{label}: valor CSS no admitido")
    if depth:
        raise ValueError(f"{label}: valor CSS no admitido")


def _validate_css_length(
    value: str | None, label: str, *, allow_none: bool = False
) -> None:
    if value is None:
        return
    _validate_css_value(value, label)
    normalized = value.strip().lower()
    if allow_none and normalized == "none":
        return
    if not _CSS_LENGTH_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label}: se requiere una medida CSS valida")


def _validate_aspect_ratio(value: str | None, label: str) -> None:
    if value is None:
        return
    _validate_css_value(value, label)
    normalized = value.strip().lower()
    if normalized == "auto":
        return
    match = re.fullmatch(
        r"(?:(auto)\s+)?(\d+(?:\.\d*)?|\.\d+)"
        r"(?:\s*/\s*(\d+(?:\.\d*)?|\.\d+))?",
        normalized,
    )
    if not match or any(
        number is not None and float(number) <= 0
        for number in match.groups()[1:]
    ):
        raise ValueError(f"{label}: se requiere una proporcion positiva")


def _validate_font_weight(value: str | None) -> None:
    if value is None:
        return
    _validate_css_value(value, "Peso de fuente")
    normalized = value.strip().lower()
    if normalized in ("normal", "bold", "bolder", "lighter"):
        return
    if not _CSS_NUMBER_PATTERN.fullmatch(normalized) or not 1 <= float(normalized) <= 1000:
        raise ValueError("Peso de fuente: use normal, bold o un numero entre 1 y 1000")


def _validate_text_alignment(value: str | None) -> None:
    if value is None:
        return
    _validate_css_value(value, "Alineacion de texto")
    if value.strip().lower() not in (
        "start", "center", "end", "left", "right", "justify", "match-parent"
    ):
        raise ValueError("Alineacion de texto no admitida")


def _validate_font_size(value: str | None) -> None:
    if value is not None and value.strip().lower() in _FONT_SIZE_KEYWORDS:
        _validate_css_value(value, "Tamano de fuente")
        return
    _validate_css_length(value, "Tamano de fuente")


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


@dataclass(frozen=True)
class ButtonDimensions:
    """Dimensiones de un boton para una orientacion concreta."""

    width: str | None = None
    height: str | None = None
    min_height: str | None = None
    aspect_ratio: str | None = None

    def __post_init__(self):
        for name in ("width", "height", "min_height"):
            _validate_css_length(getattr(self, name), name.replace("_", " ").capitalize())
        _validate_aspect_ratio(self.aspect_ratio, "Proporcion")


@dataclass(frozen=True)
class ButtonStyle:
    """CSS declarativo para el boton Streamlit renderizado por ``render_button``."""

    background: str | None = None
    background_image: str | None = None
    background_fit: Literal["cover", "contain"] = "cover"
    color: str | None = None
    padding: str | None = None
    border: str | None = None
    border_radius: str | None = None
    width: str | None = None
    aspect_ratio: str | None = None
    min_height: str | None = None
    height: str | None = None
    font_size: str | None = None
    font_weight: str | None = None
    text_align: str | None = None
    box_shadow: str | None = None
    max_width: str | None = None
    max_height: str | None = None
    fill_container: bool = False
    portrait: ButtonDimensions | None = None

    def __post_init__(self):
        if self.portrait is not None and not isinstance(
            self.portrait, ButtonDimensions
        ):
            raise ValueError("Las dimensiones verticales deben ser ButtonDimensions")
        if self.background_image is not None:
            url = urlsplit(self.background_image)
            if url.scheme not in ("http", "https") or not url.netloc or re.search(r"[\s<>\"'\\()]", self.background_image):
                raise ValueError("La imagen debe ser una URL HTTP(S) sin caracteres especiales sin codificar")
        if self.background_fit not in ("cover", "contain"):
            raise ValueError("Ajuste de imagen no admitido")
        for name in (
            "background", "color", "padding", "border", "border_radius", "box_shadow",
        ):
            _validate_css_value(getattr(self, name), name.replace("_", " ").capitalize())
        for name in ("width", "min_height", "height"):
            _validate_css_length(getattr(self, name), name.replace("_", " ").capitalize())
        for name in ("max_width", "max_height"):
            _validate_css_length(
                getattr(self, name), name.replace("_", " ").capitalize(), allow_none=True
            )
        _validate_font_size(self.font_size)
        _validate_aspect_ratio(self.aspect_ratio, "Proporcion")
        _validate_font_weight(self.font_weight)
        _validate_text_alignment(self.text_align)


@dataclass(frozen=True)
class PageStyle:
    """Ajustes comunes de estructura para una pagina Streamlit."""

    hide_header: bool = False
    content_padding: float | Insets | None = None
    content_overflow: Literal["auto", "hidden", "scroll", "visible"] | None = None
    app_overflow_y: Literal["auto", "hidden", "scroll", "visible"] | None = None
    vertical_gap_px: float | None = None
    horizontal_gap_px: float | None = None
    element_margin_px: float | None = None
    caption_margin_px: float | Insets | None = None

    def __post_init__(self):
        if self.content_padding is not None and not isinstance(
            self.content_padding, Insets
        ):
            validate_number(self.content_padding, "Padding")
        if self.caption_margin_px is not None and not isinstance(
            self.caption_margin_px, Insets
        ):
            validate_number(self.caption_margin_px, "Margen de caption")
        for value in (
            self.vertical_gap_px,
            self.horizontal_gap_px,
            self.element_margin_px,
        ):
            if value is not None:
                validate_number(value, "Espaciado")
        if self.content_overflow not in (None, "auto", "hidden", "scroll", "visible"):
            raise ValueError("Desbordamiento de contenido no admitido")
        if self.app_overflow_y not in (None, "auto", "hidden", "scroll", "visible"):
            raise ValueError("Desbordamiento de pagina no admitido")
