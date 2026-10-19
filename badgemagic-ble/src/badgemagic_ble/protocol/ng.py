"""Next-gen control protocol (service 0xF055).

Each command is one write to 0xF057: a command byte followed by parameters.
The firmware answers every known command with a one-byte result notified on
0xF056. An unknown command byte is rejected as an ATT error on the write and
gets no notification.

Encodings and result codes follow ``src/ngctrl.c`` and ``src/main.c`` of the
firmware at commit b2137f0. Where ``BadgeBLE.md`` disagrees, the code wins:

- ``power_setting 01 xx``: ``xx != 0`` enables reset-after-upload (the doc
  has it backwards), and ``02`` reboots (the doc says power off).
- ``misc``: an out-of-range brightness or a short splash-speed parameter
  returns 0xFE (the doc says 0x02); the firmware has no 10 ms minimum.
- ``flash_splash_screen``: the firmware stores the bitmap length in a uint8,
  so more than 255 bytes of XBM bits wraps around; we refuse those.
"""

from __future__ import annotations

from enum import IntEnum

from ..const import DISPLAY_COLUMNS, MAX_NAME_LENGTH
from ..exceptions import (
    CommandError,
    ConfigWriteError,
    InvalidParameterError,
    NotStreamingError,
    ProtocolError,
    UnknownSubcommandError,
)

SPLASH_MAX_WIDTH = 48
SPLASH_MAX_HEIGHT = 44
SPLASH_MAX_BITS = 255
BRIGHTNESS_LEVELS = 4
STREAM_FRAME_BYTES = DISPLAY_COLUMNS * 2


class Command(IntEnum):
    NEXT_PACKET = 0x00
    POWER_SETTING = 0x01
    STREAMING_SETTING = 0x02
    STREAM_BITMAP = 0x03
    BLE_SETTING = 0x04
    FLASH_SPLASH_SCREEN = 0x05
    SAVE_CFG = 0x06
    LOAD_FALLBACK_CFG = 0x07
    MISC = 0x08


RESULT_SUCCESS = 0x00


def _cmd(command: Command, *params: int, tail: bytes = b"") -> bytes:
    return bytes((command, *params)) + tail


# power_setting -------------------------------------------------------------


def encode_power_off() -> bytes:
    return _cmd(Command.POWER_SETTING, 0x00)


def encode_set_reset_after_upload(enabled: bool) -> bytes:
    """Reboot after each legacy upload instead of showing BLUETOOTH OFF. Needs save_cfg."""
    return _cmd(Command.POWER_SETTING, 0x01, int(bool(enabled)))


def encode_reboot() -> bytes:
    return _cmd(Command.POWER_SETTING, 0x02)


# streaming -----------------------------------------------------------------


def encode_start_streaming() -> bytes:
    """Stop animations, clear the screen and accept stream_bitmap frames."""
    return _cmd(Command.STREAMING_SETTING, 0x00)


def encode_stop_streaming() -> bytes:
    """Leave streaming: resume the stored animation if always-on BLE, else the BLE screen."""
    return _cmd(Command.STREAMING_SETTING, 0x01)


def encode_stream_bitmap(columns: bytes) -> bytes:
    """One full frame: up to 44 little-endian uint16 columns, LSB = top row.

    Shorter input is padded with blank columns so no stale pixels remain
    (the firmware only overwrites the columns it receives).
    """
    if len(columns) % 2:
        raise ValueError("columns must be whole uint16 values")
    if len(columns) > STREAM_FRAME_BYTES:
        raise ValueError(f"a frame is at most {DISPLAY_COLUMNS} columns")
    return _cmd(Command.STREAM_BITMAP, tail=bytes(columns) + bytes(STREAM_FRAME_BYTES - len(columns)))


# ble_setting ---------------------------------------------------------------


def encode_set_always_on(enabled: bool) -> bytes:
    """Keep BLE advertising outside BT-PAIRING mode. Needs save_cfg."""
    return _cmd(Command.BLE_SETTING, 0x00, int(bool(enabled)))


def encode_set_name(name: str) -> bytes:
    """Rename the badge's advertised name (1..20 bytes of UTF-8). Needs save_cfg."""
    raw = name.encode("utf-8")
    if not 1 <= len(raw) <= MAX_NAME_LENGTH:
        raise ValueError(f"name must be 1..{MAX_NAME_LENGTH} bytes of UTF-8, got {len(raw)}")
    return _cmd(Command.BLE_SETTING, 0x01, tail=raw)


# flash_splash_screen -------------------------------------------------------


def encode_set_splash(width: int, height: int, frame_height: int, bits: bytes) -> bytes:
    """Boot splash: XBM bits (rows padded to bytes, LSB leftmost). Needs save_cfg.

    The firmware scrolls the image up through the 11-row display at boot.
    """
    if not 1 <= width <= SPLASH_MAX_WIDTH:
        raise ValueError(f"splash width must be 1..{SPLASH_MAX_WIDTH}")
    if not 1 <= height <= SPLASH_MAX_HEIGHT:
        raise ValueError(f"splash height must be 1..{SPLASH_MAX_HEIGHT}")
    if not 1 <= frame_height <= 0xFF:
        raise ValueError("frame height must be 1..255")
    expected = (width + 7) // 8 * height
    if len(bits) != expected:
        raise ValueError(f"{width}x{height} XBM needs {expected} bytes, got {len(bits)}")
    if len(bits) > SPLASH_MAX_BITS:
        raise ValueError(
            f"splash bitmap is {len(bits)} bytes; firmware limit is {SPLASH_MAX_BITS} (e.g. 48x42 or 40x44)"
        )
    return _cmd(Command.FLASH_SPLASH_SCREEN, width, height, frame_height, tail=bytes(bits))


# config --------------------------------------------------------------------


def encode_save_config() -> bytes:
    return _cmd(Command.SAVE_CFG)


def encode_load_defaults() -> bytes:
    """Load firmware defaults into RAM (save_cfg to persist)."""
    return _cmd(Command.LOAD_FALLBACK_CFG)


# misc ----------------------------------------------------------------------


def encode_set_splash_speed(ms: int) -> bytes:
    """Delay between splash frames in milliseconds (uint16, little-endian). Needs save_cfg."""
    if not 0 <= ms <= 0xFFFF:
        raise ValueError("splash speed must be 0..65535 ms")
    return _cmd(Command.MISC, 0x00, tail=ms.to_bytes(2, "little"))


def encode_set_brightness(level: int) -> bytes:
    """LED brightness 0..3, applied immediately (save_cfg to persist)."""
    if not 0 <= level < BRIGHTNESS_LEVELS:
        raise ValueError(f"brightness must be 0..{BRIGHTNESS_LEVELS - 1}")
    return _cmd(Command.MISC, 0x01, level)


def encode_next_packet() -> bytes:
    """Reserved; a stub in the firmware that always succeeds."""
    return _cmd(Command.NEXT_PACKET)


# results -------------------------------------------------------------------

# Per-command meaning of non-zero result codes (int8 values seen as uint8).
_RESULTS: dict[Command, dict[int, tuple[type[CommandError], str]]] = {
    Command.POWER_SETTING: {
        0xFF: (UnknownSubcommandError, "unknown power_setting sub-command"),
        0xFE: (InvalidParameterError, "power_setting needs a parameter"),
    },
    Command.STREAMING_SETTING: {},
    Command.STREAM_BITMAP: {
        0xFF: (NotStreamingError, "badge is not in streaming mode"),
    },
    Command.BLE_SETTING: {
        0xFF: (UnknownSubcommandError, "unknown ble_setting sub-command"),
        0xFE: (InvalidParameterError, "ble_setting needs a parameter"),
    },
    Command.FLASH_SPLASH_SCREEN: {
        0xFF: (InvalidParameterError, "splash width too large"),
        0xFE: (InvalidParameterError, "splash height too large"),
        0xFD: (InvalidParameterError, "splash bitmap too large"),
        0xFC: (InvalidParameterError, "splash header incomplete"),
    },
    Command.MISC: {
        0xFF: (UnknownSubcommandError, "unknown misc sub-command"),
        0xFE: (InvalidParameterError, "misc parameter missing or out of range"),
    },
}


def decode_result(command: Command | int, data: bytes) -> None:
    """Check a result notification; raise a typed ``CommandError`` if it failed."""
    if len(data) != 1:
        raise ProtocolError(f"expected a 1-byte result, got {data.hex() or 'nothing'}")
    code = data[0]
    if code == RESULT_SUCCESS:
        return
    try:
        cmd = Command(command)
    except ValueError:
        raise CommandError(int(command), code) from None
    if cmd is Command.SAVE_CFG:
        raise ConfigWriteError(cmd, code, "writing config to flash failed")
    exc_type, message = _RESULTS.get(cmd, {}).get(code, (CommandError, "command failed"))
    raise exc_type(cmd, code, message)
