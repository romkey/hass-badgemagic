"""Legacy "wang" upload frame (service 0xFEE0, characteristic 0xFEE1).

Frame layout (all offsets in bytes)::

    0x00  5   b"wang\\0"
    0x05  1   OEM brightness (0x00 100%, 0x10 75%, 0x20 50%, 0x40 25%)
    0x06  1   flash bitfield, bit i = message i
    0x07  1   marquee bitfield, bit i = message i
    0x08  8   mode byte per message: speed (0..7) << 4 | mode (0..8)
    0x10  16  size per message, big-endian uint16, in 8-column chunks
    0x20  6   zero
    0x26  6   timestamp: year % 256, month, day, hour, minute, second
    0x2C  4   zero
    0x30  16  zero
    0x40  ... bitmap chunks, 11 bytes each; then zero padding to 16 bytes

The frame is sent as consecutive 16-byte writes with response. No I/O here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

from ..render import Bitmap

MAGIC = b"wang\x00"
HEADER_SIZE = 64
PACKET_SIZE = 16
MAX_MESSAGES = 8
# The frame is stored in EEPROM from offset 0 and the game high score lives at
# 0x7FF8; the OEM app caps uploads at 8 KiB, and so do we.
MAX_FRAME_SIZE = 8192

MIN_SPEED = 1
MAX_SPEED = 8


class Mode(IntEnum):
    SCROLL_LEFT = 0
    SCROLL_RIGHT = 1
    SCROLL_UP = 2
    SCROLL_DOWN = 3
    FIXED = 4
    ANIMATION = 5
    SNOWFLAKE = 6
    PICTURE = 7
    LASER = 8


class OemBrightness(IntEnum):
    """Header byte 5. OEM firmware only; the open firmware ignores it."""

    FULL = 0x00
    THREE_QUARTERS = 0x10
    HALF = 0x20
    QUARTER = 0x40


@dataclass(frozen=True, slots=True)
class Message:
    """One of up to 8 messages in an upload.

    ``speed`` is 1 (slowest) to 8 (fastest) and maps to the firmware's 0..7.
    """

    bitmap: Bitmap
    mode: Mode = Mode.SCROLL_LEFT
    speed: int = 4
    flash: bool = False
    marquee: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", Mode(self.mode))
        if not MIN_SPEED <= self.speed <= MAX_SPEED:
            raise ValueError(f"speed must be {MIN_SPEED}..{MAX_SPEED}, got {self.speed}")
        if self.bitmap.width <= 0:
            raise ValueError("message bitmap is empty")

    @property
    def mode_byte(self) -> int:
        return ((self.speed - 1) << 4) | int(self.mode)


def encode_timestamp(timestamp: dt.datetime | None) -> bytes:
    """Six timestamp bytes; ``None`` gives zeros.

    Byte 0 is ``year % 256``: the open firmware decodes the year as
    ``2000 + (ts[0] - 208) mod 256``, so 2026 is sent as 234.
    """
    if timestamp is None:
        return bytes(6)
    t = timestamp
    return bytes((t.year % 256, t.month, t.day, t.hour, t.minute, t.second))


def encode_frame(
    messages: Sequence[Message],
    timestamp: dt.datetime | None,
    brightness: OemBrightness | int | None = None,
) -> bytes:
    """Encode up to 8 messages as one upload frame, zero-padded to 16 bytes.

    ``timestamp`` sets the badge clock on the open firmware. ``None`` sends
    zeros, which the open firmware still loads into its clock (as year 2048),
    so pass a real time unless the badge runs OEM firmware.
    """
    if not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError(f"an upload holds 1..{MAX_MESSAGES} messages, got {len(messages)}")
    level = 0 if brightness is None else int(brightness)
    if not 0 <= level <= 0xFF:
        raise ValueError("brightness byte must be 0..255")

    header = bytearray(HEADER_SIZE)
    header[0:5] = MAGIC
    header[5] = level
    data = bytearray()
    for i, msg in enumerate(messages):
        if msg.flash:
            header[6] |= 1 << i
        if msg.marquee:
            header[7] |= 1 << i
        header[8 + i] = msg.mode_byte
        chunks = msg.bitmap.to_column_chunks()
        header[0x10 + 2 * i : 0x12 + 2 * i] = (len(chunks) // 11).to_bytes(2, "big")
        data += chunks
    header[0x26:0x2C] = encode_timestamp(timestamp)

    frame = bytes(header) + bytes(data)
    frame += bytes(-len(frame) % PACKET_SIZE)
    if len(frame) > MAX_FRAME_SIZE:
        raise ValueError(f"frame is {len(frame)} bytes; the badge accepts at most {MAX_FRAME_SIZE}")
    return frame


def chunk16(data: bytes) -> list[bytes]:
    """Split ``data`` into 16-byte packets, zero-padding the last one."""
    padded = bytes(data) + bytes(-len(data) % PACKET_SIZE)
    return [padded[i : i + PACKET_SIZE] for i in range(0, len(padded), PACKET_SIZE)]


def encode_pin(pin: str | int) -> bytes:
    """The 16-byte PIN packet: 4 ASCII digits then 12 zero bytes."""
    text = f"{pin:04d}" if isinstance(pin, int) else str(pin)
    if len(text) != 4 or not text.isascii() or not text.isdigit():
        raise ValueError("PIN must be exactly 4 digits")
    return text.encode("ascii") + bytes(PACKET_SIZE - 4)
