"""Text and image rendering to 11-row badge bitmaps.

Text is drawn from the bundled public-domain X.Org misc-misc BDF fonts (see
``fonts/README``) by a small pure-Python BDF parser. Pillow is only imported
for image input and output.
"""

from __future__ import annotations

import functools
import io
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

from .const import DISPLAY_COLUMNS, DISPLAY_ROWS

if TYPE_CHECKING:
    from PIL import Image

ROWS = DISPLAY_ROWS

FONT_FILES: dict[str, str] = {
    "normal": "6x10.bdf",
    "compact": "5x8.bdf",
    "tiny": "4x6.bdf",
}
FONT_NAMES: tuple[str, ...] = tuple(FONT_FILES)
DEFAULT_FONT = "normal"
FALLBACK_CHAR = "?"

SPLASH_MAX_WIDTH = 48
SPLASH_MAX_HEIGHT = 44


# --------------------------------------------------------------------------
# Bitmap
# --------------------------------------------------------------------------


class Bitmap:
    """A ``width x 11`` grid of pixels; ``True`` is a lit LED.

    ``bitmap[x, y]`` reads or writes one pixel, with ``x`` the column from the
    left and ``y`` the row from the top.
    """

    __slots__ = ("_rows", "_width")

    def __init__(self, width: int) -> None:
        if width < 0:
            raise ValueError("width must not be negative")
        self._width = width
        self._rows = [bytearray(width) for _ in range(ROWS)]

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[Any]]) -> Bitmap:
        """Build a bitmap from 11 equally long rows of truthy/falsy values."""
        if len(rows) != ROWS:
            raise ValueError(f"expected {ROWS} rows, got {len(rows)}")
        width = len(rows[0])
        bitmap = cls(width)
        for y, row in enumerate(rows):
            if len(row) != width:
                raise ValueError("all rows must have the same length")
            bitmap._rows[y][:] = bytes(1 if v else 0 for v in row)
        return bitmap

    @classmethod
    def from_strings(cls, lines: Sequence[str], on: str = "#") -> Bitmap:
        """Build a bitmap from 11 strings; characters in ``on`` are lit."""
        return cls.from_rows([[c in on for c in line] for line in lines])

    @classmethod
    def hconcat(cls, bitmaps: Iterable[Bitmap], gap: int = 0) -> Bitmap:
        """Join bitmaps left to right with ``gap`` blank columns between them."""
        parts = list(bitmaps)
        if not parts:
            return cls(0)
        out = cls(sum(b.width for b in parts) + gap * (len(parts) - 1))
        x = 0
        for part in parts:
            for y in range(ROWS):
                out._rows[y][x : x + part.width] = part._rows[y]
            x += part.width + gap
        return out

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return ROWS

    def __getitem__(self, xy: tuple[int, int]) -> bool:
        x, y = xy
        return bool(self._rows[y][x])

    def __setitem__(self, xy: tuple[int, int], value: bool) -> None:
        x, y = xy
        self._rows[y][x] = 1 if value else 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Bitmap):
            return NotImplemented
        return self._width == other._width and self._rows == other._rows

    def __repr__(self) -> str:
        return f"Bitmap(width={self._width})"

    def rows(self) -> list[list[bool]]:
        """The pixels as 11 lists of booleans, top row first."""
        return [[bool(v) for v in row] for row in self._rows]

    def crop(self, x: int, width: int) -> Bitmap:
        """A ``width``-column window starting at column ``x``.

        Columns outside this bitmap are blank, so ``x`` may be negative or run
        past the right edge (useful for scrolling).
        """
        out = Bitmap(width)
        lo = max(0, x)
        hi = min(self._width, x + width)
        if lo < hi:
            for y in range(ROWS):
                out._rows[y][lo - x : hi - x] = self._rows[y][lo:hi]
        return out

    def pad(self, width: int, align: str = "left") -> Bitmap:
        """Pad with blank columns to ``width``; ``align`` is left, center or right."""
        if width <= self._width:
            return self.copy()
        extra = width - self._width
        left = {"left": 0, "center": extra // 2, "right": extra}[align]
        return self.crop(-left, width)

    def copy(self) -> Bitmap:
        return self.crop(0, self._width)

    def to_column_chunks(self) -> bytes:
        """Legacy upload encoding.

        The width is padded with blank columns to a multiple of 8. Each 8-wide
        chunk becomes 11 bytes, top row first, with the MSB the leftmost pixel.
        """
        chunks = (self._width + 7) // 8
        out = bytearray(chunks * ROWS)
        for c in range(chunks):
            x0 = c * 8
            for y in range(ROWS):
                row = self._rows[y]
                byte = 0
                for i in range(8):
                    x = x0 + i
                    if x < self._width and row[x]:
                        byte |= 0x80 >> i
                out[c * ROWS + y] = byte
        return bytes(out)

    @property
    def chunk_count(self) -> int:
        """Number of 8-column chunks in the legacy encoding."""
        return (self._width + 7) // 8

    def to_columns(self) -> bytes:
        """Streaming encoding: one little-endian uint16 per column, LSB = top row."""
        out = bytearray(self._width * 2)
        for x in range(self._width):
            value = 0
            for y in range(ROWS):
                if self._rows[y][x]:
                    value |= 1 << y
            out[2 * x] = value & 0xFF
            out[2 * x + 1] = value >> 8
        return bytes(out)

    def to_ascii(self, on: str = "#", off: str = ".") -> str:
        return "\n".join(
            "".join(on if v else off for v in row) for row in self._rows
        )

    def to_image(self, scale: int = 1) -> Image.Image:
        """A black-and-white Pillow image (lit pixels white), scaled up by ``scale``."""
        from PIL import Image

        img = Image.new("1", (max(1, self._width), ROWS))
        img.putdata([v for row in self._rows for v in row] or [0] * ROWS)
        if scale != 1:
            img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
        return img


# --------------------------------------------------------------------------
# BDF fonts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Glyph:
    """One BDF glyph.

    ``rows`` holds ``height`` integers, top row first; bit ``width - 1 - i``
    of a row is pixel ``i`` from the left of the glyph's bounding box.
    """

    encoding: int
    dwidth: int
    width: int
    height: int
    x_offset: int
    y_offset: int
    rows: tuple[int, ...]

    def pixel(self, i: int, j: int) -> bool:
        return bool((self.rows[j] >> (self.width - 1 - i)) & 1)


@dataclass(frozen=True, slots=True)
class BdfFont:
    """A parsed BDF font; ``glyphs`` maps Unicode code points to glyphs."""

    name: str
    bbox_width: int
    bbox_height: int
    bbox_x: int
    bbox_y: int
    ascent: int
    descent: int
    default_char: int | None
    glyphs: dict[int, Glyph]

    @classmethod
    def parse(cls, text: str) -> BdfFont:
        props: dict[str, list[str]] = {}
        glyphs: dict[int, Glyph] = {}
        lines = iter(text.splitlines())
        for line in lines:
            keyword, _, rest = line.partition(" ")
            if keyword == "STARTCHAR":
                glyph = _parse_glyph(lines)
                if glyph is not None:
                    glyphs[glyph.encoding] = glyph
            elif keyword in ("FONT", "FONTBOUNDINGBOX", "FONT_ASCENT", "FONT_DESCENT", "DEFAULT_CHAR"):
                props[keyword] = rest.split()
        if "FONTBOUNDINGBOX" not in props:
            raise ValueError("BDF font has no FONTBOUNDINGBOX")
        bw, bh, bx, by = (int(v) for v in props["FONTBOUNDINGBOX"])
        default = props.get("DEFAULT_CHAR")
        return cls(
            name=" ".join(props.get("FONT", ["?"])),
            bbox_width=bw,
            bbox_height=bh,
            bbox_x=bx,
            bbox_y=by,
            ascent=int(props.get("FONT_ASCENT", [bh + by])[0]),
            descent=int(props.get("FONT_DESCENT", [-by])[0]),
            default_char=int(default[0]) if default else None,
            glyphs=glyphs,
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> BdfFont:
        with open(path, encoding="latin-1") as f:
            return cls.parse(f.read())

    def glyph(self, codepoint: int) -> Glyph | None:
        return self.glyphs.get(codepoint)

    def glyph_or_fallback(self, codepoint: int) -> Glyph | None:
        """The glyph for ``codepoint``, else ``?``, else DEFAULT_CHAR, else None."""
        g = self.glyphs.get(codepoint)
        if g is None:
            g = self.glyphs.get(ord(FALLBACK_CHAR))
        if g is None and self.default_char is not None:
            g = self.glyphs.get(self.default_char)
        return g


def _parse_glyph(lines: Iterable[str]) -> Glyph | None:
    encoding = -1
    dwidth = 0
    bbx = (0, 0, 0, 0)
    rows: list[int] = []
    in_bitmap = False
    for line in lines:
        if in_bitmap:
            if line.startswith("ENDCHAR"):
                break
            hex_row = line.strip()
            value = int(hex_row, 16)
            # Rows are padded to whole bytes on the right; keep the leftmost
            # ``width`` bits, right-aligned.
            shift = len(hex_row) * 4 - bbx[0]
            rows.append(value >> shift if shift >= 0 else value << -shift)
            continue
        keyword, _, rest = line.partition(" ")
        if keyword == "ENCODING":
            # "ENCODING -1 n" marks a non-Unicode glyph; those are skipped.
            encoding = int(rest.split()[0])
        elif keyword == "DWIDTH":
            dwidth = int(rest.split()[0])
        elif keyword == "BBX":
            w, h, x, y = (int(v) for v in rest.split())
            bbx = (w, h, x, y)
        elif keyword == "BITMAP":
            in_bitmap = True
        elif keyword == "ENDCHAR":
            break
    if encoding < 0:
        return None
    w, h, x, y = bbx
    rows = (rows + [0] * h)[:h]
    return Glyph(encoding, dwidth, w, h, x, y, tuple(rows))


@functools.cache
def get_font(name: str = DEFAULT_FONT) -> BdfFont:
    """Load one of the bundled fonts (``normal``, ``compact``, ``tiny``), cached."""
    try:
        filename = FONT_FILES[name]
    except KeyError:
        raise ValueError(f"unknown font {name!r}; choose one of {', '.join(FONT_NAMES)}") from None
    data = resources.files(__package__).joinpath("fonts", filename).read_text(encoding="latin-1")
    return BdfFont.parse(data)


def render_text(text: str, font: str | BdfFont = DEFAULT_FONT) -> Bitmap:
    """Render ``text`` as one line on the 11-row display.

    Glyphs advance by their ``DWIDTH`` (the cells already include 1 px of
    spacing) and sit on a common baseline. The font's cell is centered
    vertically in the 11 rows, rounding toward the top. Characters the font
    lacks render as ``?``. Newlines and tabs become spaces.
    """
    bdf = font if isinstance(font, BdfFont) else get_font(font)
    if bdf.bbox_height > ROWS:
        raise ValueError(f"font cell is {bdf.bbox_height} px high; the badge has {ROWS} rows")
    top = (ROWS - bdf.bbox_height) // 2
    # Row in the bitmap of the baseline (y = 0 in font coordinates).
    baseline_row = top + bdf.bbox_height + bdf.bbox_y

    placed: list[tuple[int, Glyph]] = []
    pen = 0
    right = 0
    for ch in text.replace("\t", " ").replace("\r", "").replace("\n", " "):
        glyph = bdf.glyph_or_fallback(ord(ch))
        if glyph is None:
            pen += bdf.bbox_width
            continue
        placed.append((pen, glyph))
        right = max(right, pen + glyph.x_offset + glyph.width)
        pen += glyph.dwidth
    bitmap = Bitmap(max(pen, right))

    for origin, g in placed:
        glyph_top = baseline_row - (g.y_offset + g.height)
        for j in range(g.height):
            y = glyph_top + j
            if not 0 <= y < ROWS:
                continue
            for i in range(g.width):
                x = origin + g.x_offset + i
                if 0 <= x < bitmap.width and g.pixel(i, j):
                    bitmap[x, y] = True
    return bitmap


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def _open_grayscale(source: Any) -> Image.Image:
    from PIL import Image

    if isinstance(source, Image.Image):
        img = source
    elif isinstance(source, bytes | bytearray):
        img = Image.open(io.BytesIO(source))
    else:
        img = Image.open(source)
    if getattr(img, "is_animated", False):
        img.seek(0)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        img = Image.alpha_composite(background, rgba)
    return img.convert("L")


def _threshold(img: Image.Image, threshold: int, invert: bool) -> list[list[bool]]:
    data = img.tobytes()  # mode "L": one byte per pixel
    w = img.width
    return [
        [(px >= threshold) != invert for px in data[y * w : (y + 1) * w]]
        for y in range(img.height)
    ]


def load_image(source: Any, *, threshold: int = 128, invert: bool = False) -> Bitmap:
    """Load any Pillow-readable image as an 11-row bitmap.

    Images taller than 11 px are scaled down to 11 px keeping the aspect
    ratio; shorter images are centered vertically (rounding toward the top)
    so small pixel art stays crisp. Transparent areas are treated as black.
    Pixels at or above ``threshold`` (0..255 grayscale) are lit; ``invert``
    lights the dark pixels instead.
    """
    from PIL import Image

    img = _open_grayscale(source)
    if img.height > ROWS:
        new_width = max(1, round(img.width * ROWS / img.height))
        img = img.resize((new_width, ROWS), Image.Resampling.LANCZOS)
    pixels = _threshold(img, threshold, invert)
    top = (ROWS - img.height) // 2
    rows = [[False] * img.width for _ in range(ROWS)]
    for j, row in enumerate(pixels):
        rows[top + j] = row
    return Bitmap.from_rows(rows)


# --------------------------------------------------------------------------
# XBM (splash screens)
# --------------------------------------------------------------------------

_XBM_DEFINE = re.compile(r"#define\s+(\S*?)(width|height)\s+(\d+)")
_XBM_BITS = re.compile(r"\{([^}]*)\}", re.S)


@dataclass(frozen=True, slots=True)
class Xbm:
    """An X bitmap: rows padded to whole bytes, LSB the leftmost pixel."""

    width: int
    height: int
    bits: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("XBM width and height must be positive")
        if len(self.bits) != self.stride * self.height:
            raise ValueError(
                f"XBM {self.width}x{self.height} needs {self.stride * self.height} bytes, got {len(self.bits)}"
            )

    @property
    def stride(self) -> int:
        return (self.width + 7) // 8

    def __getitem__(self, xy: tuple[int, int]) -> bool:
        x, y = xy
        return bool((self.bits[y * self.stride + x // 8] >> (x % 8)) & 1)

    @classmethod
    def from_pixels(cls, rows: Sequence[Sequence[Any]]) -> Xbm:
        height = len(rows)
        width = len(rows[0]) if rows else 0
        stride = (width + 7) // 8
        bits = bytearray(stride * height)
        for y, row in enumerate(rows):
            for x, v in enumerate(row):
                if v:
                    bits[y * stride + x // 8] |= 1 << (x % 8)
        return cls(width, height, bytes(bits))

    @classmethod
    def from_bitmap(cls, bitmap: Bitmap) -> Xbm:
        return cls.from_pixels(bitmap.rows())

    @classmethod
    def from_image(
        cls,
        source: Any,
        *,
        threshold: int = 128,
        invert: bool = False,
        max_width: int = SPLASH_MAX_WIDTH,
        max_height: int = SPLASH_MAX_HEIGHT,
    ) -> Xbm:
        """Load an image at its own size, scaled down only to fit the limits."""
        from PIL import Image

        img = _open_grayscale(source)
        if img.width > max_width or img.height > max_height:
            factor = min(max_width / img.width, max_height / img.height)
            size = (max(1, int(img.width * factor)), max(1, int(img.height * factor)))
            img = img.resize(size, Image.Resampling.LANCZOS)
        return cls.from_pixels(_threshold(img, threshold, invert))

    def rows(self) -> list[list[bool]]:
        return [[self[x, y] for x in range(self.width)] for y in range(self.height)]

    def to_bitmap(self, top: int = 0) -> Bitmap:
        """Rows ``top .. top+10`` as a Bitmap (rows past the end are blank)."""
        rows = self.rows()
        blank = [False] * self.width
        return Bitmap.from_rows([rows[y] if 0 <= y < self.height else blank for y in range(top, top + ROWS)])

    def to_ascii(self, on: str = "#", off: str = ".") -> str:
        return "\n".join("".join(on if v else off for v in row) for row in self.rows())


def parse_xbm(text: str) -> Xbm:
    """Parse the C source form of an XBM file."""
    dims: dict[str, int] = {}
    for _prefix, which, value in _XBM_DEFINE.findall(text):
        dims.setdefault(which, int(value))
    if "width" not in dims or "height" not in dims:
        raise ValueError("XBM is missing #define ..._width / ..._height")
    match = _XBM_BITS.search(text)
    if match is None:
        raise ValueError("XBM has no bits array")
    values = [v.strip() for v in match.group(1).split(",")]
    data = bytes(int(v, 0) & 0xFF for v in values if v)
    width, height = dims["width"], dims["height"]
    stride = (width + 7) // 8
    if len(data) < stride * height:
        raise ValueError(f"XBM bits array has {len(data)} bytes, expected {stride * height}")
    return Xbm(width, height, data[: stride * height])


def fit_columns(bitmap: Bitmap, width: int = DISPLAY_COLUMNS, align: str = "center") -> Bitmap:
    """Pad or clip ``bitmap`` to exactly ``width`` columns (for one streamed frame)."""
    if bitmap.width >= width:
        return bitmap.crop(0, width)
    return bitmap.pad(width, align)
