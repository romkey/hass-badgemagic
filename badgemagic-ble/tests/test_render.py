"""Bitmap conversions, BDF fonts, images and XBM."""

from __future__ import annotations

import io
from importlib import resources

import pytest
from PIL import Image

from badgemagic_ble.render import (
    FONT_FILES,
    ROWS,
    BdfFont,
    Bitmap,
    Xbm,
    fit_columns,
    get_font,
    load_image,
    parse_xbm,
    render_text,
)

# --- Bitmap conversions -----------------------------------------------------


def test_to_column_chunks_msb_leftmost_top_to_bottom() -> None:
    bm = Bitmap(10)
    bm[0, 0] = True  # chunk 0 row 0 MSB
    bm[7, 1] = True  # chunk 0 row 1 LSB
    bm[3, 10] = True  # chunk 0 row 10
    bm[8, 5] = True  # chunk 1 row 5 MSB
    bm[9, 5] = True
    out = bm.to_column_chunks()
    assert len(out) == 22
    assert out[0] == 0x80
    assert out[1] == 0x01
    assert out[10] == 0x10
    assert out[11 + 5] == 0xC0
    assert sum(out) == 0x80 + 0x01 + 0x10 + 0xC0


def test_to_column_chunks_pads_width() -> None:
    assert Bitmap(0).to_column_chunks() == b""
    assert len(Bitmap(1).to_column_chunks()) == 11
    assert len(Bitmap(8).to_column_chunks()) == 11
    assert len(Bitmap(9).to_column_chunks()) == 22
    assert Bitmap(44).chunk_count == 6


def test_to_columns_little_endian_lsb_top() -> None:
    bm = Bitmap(3)
    bm[0, 0] = True  # 0x0001
    for y in range(ROWS):
        bm[1, y] = True  # 0x07FF
    bm[2, 10] = True  # 0x0400
    bm[2, 8] = True  # 0x0100
    assert bm.to_columns() == bytes([0x01, 0x00, 0xFF, 0x07, 0x00, 0x05])


def test_from_strings_and_ascii_roundtrip() -> None:
    lines = ["#." * 3] * ROWS
    bm = Bitmap.from_strings(lines)
    assert bm.width == 6
    assert bm[0, 0] and not bm[1, 0]
    assert bm.to_ascii().splitlines() == lines


def test_from_rows_validation() -> None:
    with pytest.raises(ValueError):
        Bitmap.from_rows([[1]] * 10)
    with pytest.raises(ValueError):
        Bitmap.from_rows([[1]] * 10 + [[1, 0]])


def test_crop_pad_concat() -> None:
    bm = Bitmap(2)
    bm[0, 0] = True
    bm[1, 1] = True
    assert bm.crop(-1, 4).rows()[0] == [False, True, False, False]
    assert bm.crop(1, 1).rows()[1] == [True]
    assert bm.pad(6, "center").rows()[0] == [False, False, True, False, False, False]
    assert bm.pad(4, "right").rows()[1] == [False, False, False, True]
    joined = Bitmap.hconcat([bm, bm], gap=1)
    assert joined.width == 5
    assert joined.rows()[0] == [True, False, False, True, False]
    assert fit_columns(Bitmap(50)).width == 44
    assert fit_columns(Bitmap(10)).width == 44


def test_to_image() -> None:
    bm = Bitmap(2)
    bm[1, 3] = True
    img = bm.to_image(scale=2)
    assert img.size == (4, 22)
    assert img.getpixel((2, 6)) and not img.getpixel((0, 0))


# --- BDF fonts --------------------------------------------------------------


def raw_glyph(filename: str, startchar: str) -> tuple[list[str], tuple[int, int, int, int]]:
    """Read a glyph's BITMAP hex rows and BBX straight from the bundled file."""
    text = resources.files("badgemagic_ble").joinpath("fonts", filename).read_text(encoding="latin-1")
    block = text.split(f"\nSTARTCHAR {startchar}\n", 1)[1].split("ENDCHAR", 1)[0]
    bbx = next(line for line in block.splitlines() if line.startswith("BBX "))
    rows = block.split("BITMAP\n", 1)[1].split()
    w, h, x, y = (int(v) for v in bbx.split()[1:])
    return rows, (w, h, x, y)


# The "A" glyphs as published in the X.Org files, for an independent check.
EXPECTED_A = {
    "normal": ["00", "20", "50", "88", "88", "F8", "88", "88", "00", "00"],
    "compact": ["00", "60", "90", "90", "F0", "90", "90", "00"],
    "tiny": ["40", "A0", "E0", "A0", "A0", "00"],
}
# Top row of the font cell in the 11-row display.
EXPECTED_TOP = {"normal": 0, "compact": 1, "tiny": 2}


@pytest.mark.parametrize("font", list(FONT_FILES))
def test_glyph_A_matches_bdf(font: str) -> None:
    rows, (w, h, _x, _y) = raw_glyph(FONT_FILES[font], "A")
    assert rows == EXPECTED_A[font]
    bm = render_text("A", font)
    assert bm.width == w
    top = EXPECTED_TOP[font]
    for j, hex_row in enumerate(rows):
        value = int(hex_row, 16)
        expected = [bool(value & (0x80 >> i)) for i in range(w)]
        assert bm.rows()[top + j] == expected, f"{font} row {j}"
    # Everything outside the cell is blank.
    for y in list(range(top)) + list(range(top + h, ROWS)):
        assert not any(bm.rows()[y])


@pytest.mark.parametrize(("font", "cell"), [("normal", (6, 10)), ("compact", (5, 8)), ("tiny", (4, 6))])
def test_font_metrics(font: str, cell: tuple[int, int]) -> None:
    f = get_font(font)
    assert (f.bbox_width, f.bbox_height) == cell
    assert ord("A") in f.glyphs
    assert get_font(font) is f  # cached


def test_glyph_counts_match_plan() -> None:
    assert len(get_font("normal").glyphs) == 1597
    assert len(get_font("compact").glyphs) == 1426
    assert len(get_font("tiny").glyphs) == 919


def test_dwidth_advance() -> None:
    assert render_text("AB", "normal").width == 12
    assert render_text("AB", "compact").width == 10
    assert render_text("AB", "tiny").width == 8
    assert render_text("", "normal").width == 0


def test_common_baseline_descender() -> None:
    # "g" descends below the baseline; its lowest pixel row is the cell's bottom.
    bm = render_text("Ag", "normal")
    rows, _ = raw_glyph("6x10.bdf", "g")
    for j, hex_row in enumerate(rows):
        value = int(hex_row, 16)
        assert bm.rows()[j][6:12] == [bool(value & (0x80 >> i)) for i in range(6)]


def test_missing_glyph_renders_question_mark() -> None:
    for font in FONT_FILES:
        assert render_text("✓", font) == render_text("?", font)  # check mark is absent
        assert render_text("A\U0001f600B", font) == render_text("A?B", font)


def test_newlines_become_spaces() -> None:
    assert render_text("A\nB") == render_text("A B")


def test_unknown_font() -> None:
    with pytest.raises(ValueError):
        render_text("A", "huge")


SYNTHETIC_BDF = """\
STARTFONT 2.1
FONT -test-synthetic
SIZE 7 75 75
FONTBOUNDINGBOX 4 7 0 -2
STARTPROPERTIES 2
FONT_ASCENT 5
FONT_DESCENT 2
ENDPROPERTIES
CHARS 3
STARTCHAR dot
ENCODING 46
SWIDTH 500 0
DWIDTH 2 0
BBX 1 1 0 0
BITMAP
80
ENDCHAR
STARTCHAR bar
ENCODING 124
SWIDTH 500 0
DWIDTH 3 0
BBX 1 7 1 -2
BITMAP
80
80
80
80
80
80
80
ENDCHAR
STARTCHAR wide
ENCODING 87
SWIDTH 500 0
DWIDTH 4 0
BBX 12 1 0 4
BITMAP
8010
ENDCHAR
ENDFONT
"""


def test_vertical_placement_with_offsets() -> None:
    font = BdfFont.parse(SYNTHETIC_BDF)
    assert font.ascent == 5 and font.descent == 2
    bm = render_text(".|", font)
    # Cell is 7 rows: (11 - 7) // 2 = 2 blank rows on top. Cell top is y = 5
    # (bbox_y + height), so the baseline (y = 0) is display row 2 + 5 = 7.
    # The dot sits just above the baseline: row 6.
    assert [y for y in range(ROWS) if bm[0, y]] == [6]
    # The bar spans y = -2 .. 4, i.e. display rows 2 .. 8, and is offset 1 px right.
    assert [y for y in range(ROWS) if bm[3, y]] == [2, 3, 4, 5, 6, 7, 8]
    assert not any(bm[2, y] for y in range(ROWS))
    assert bm.width == 5


def test_glyph_wider_than_advance_extends_bitmap() -> None:
    font = BdfFont.parse(SYNTHETIC_BDF)
    bm = render_text("W", font)
    assert bm.width == 12
    assert bm[0, 2] and bm[11, 2]  # top row of cell is y = 4 -> display row 2


def test_missing_glyph_without_question_mark() -> None:
    font = BdfFont.parse(SYNTHETIC_BDF)
    bm = render_text("x.", font)
    assert bm.width == 4 + 2  # blank cell width, then the dot


def test_rounding_toward_top() -> None:
    # A 10-row cell leaves one spare row; it goes at the bottom.
    bm = render_text("|" * 3, "normal")
    assert not any(bm.rows()[10])


# --- Images -----------------------------------------------------------------


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_load_image_threshold_and_center() -> None:
    img = Image.new("L", (3, 5), 0)
    img.putpixel((0, 0), 255)
    img.putpixel((2, 4), 127)
    bm = load_image(png_bytes(img))
    assert bm.width == 3
    # (11 - 5) // 2 = 3 rows of padding on top.
    assert bm[0, 3]
    assert not bm[2, 7]
    assert load_image(img, threshold=100)[2, 7]
    assert not load_image(img, invert=True)[0, 3]


def test_load_image_scales_tall_images() -> None:
    img = Image.new("RGB", (44, 22), (255, 255, 255))
    bm = load_image(img)
    assert bm.width == 22
    assert all(all(row) for row in bm.rows())


def test_load_image_transparent_is_off() -> None:
    img = Image.new("RGBA", (2, 11), (255, 255, 255, 0))
    img.putpixel((1, 0), (255, 255, 255, 255))
    bm = load_image(img)
    assert not bm[0, 0]
    assert bm[1, 0]


# --- XBM --------------------------------------------------------------------

XBM_TEXT = """\
#define test_width 10
#define test_height 2
static unsigned char test_bits[] = {
   0x01, 0x02, 0x80, 0x00 };
"""


def test_parse_xbm_lsb_first() -> None:
    xbm = parse_xbm(XBM_TEXT)
    assert (xbm.width, xbm.height, xbm.stride) == (10, 2, 2)
    assert xbm[0, 0]
    assert xbm[9, 0]
    assert xbm[7, 1]
    assert sum(xbm[x, y] for x in range(10) for y in range(2)) == 3


def test_xbm_from_bitmap_roundtrip() -> None:
    bm = render_text("Hi", "tiny")
    xbm = Xbm.from_bitmap(bm)
    assert xbm.height == ROWS
    assert xbm.to_bitmap() == bm


def test_xbm_from_image_keeps_size_until_limit() -> None:
    assert (Xbm.from_image(Image.new("L", (44, 22), 255)).width, Xbm.from_image(Image.new("L", (44, 22))).height) == (44, 22)
    big = Xbm.from_image(Image.new("L", (96, 44), 255))
    assert (big.width, big.height) == (48, 22)


def test_parse_xbm_errors() -> None:
    with pytest.raises(ValueError):
        parse_xbm("static char x[] = {0};")
    with pytest.raises(ValueError):
        parse_xbm("#define a_width 8\n#define a_height 2\nstatic char a_bits[] = {0x00};")
