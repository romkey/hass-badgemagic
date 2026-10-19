"""Render a badge bitmap as a PNG that looks like the LED matrix."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .badgemagic_ble import Bitmap
from .badgemagic_ble.render import fit_columns
from .const import DISPLAY_COLUMNS

PITCH = 10  # pixels per LED
LED = 8  # LED diameter
MARGIN = 10
BACKGROUND = (12, 12, 14)
LED_OFF = (40, 24, 22)
LED_ON = (255, 72, 40)


def render_png(bitmap: Bitmap | None) -> bytes:
    """A PNG of ``bitmap``, at least the display's 44 columns wide (centered).

    Wider bitmaps (scrolling messages) are drawn at full width. ``None``
    draws a blank display.
    """
    if bitmap is None:
        bitmap = Bitmap(DISPLAY_COLUMNS)
    elif bitmap.width < DISPLAY_COLUMNS:
        bitmap = fit_columns(bitmap, DISPLAY_COLUMNS, "center")
    width = MARGIN * 2 + bitmap.width * PITCH
    height = MARGIN * 2 + bitmap.height * PITCH
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    inset = (PITCH - LED) / 2
    for y, row in enumerate(bitmap.rows()):
        for x, lit in enumerate(row):
            left = MARGIN + x * PITCH + inset
            top = MARGIN + y * PITCH + inset
            draw.ellipse((left, top, left + LED, top + LED), fill=LED_ON if lit else LED_OFF)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
