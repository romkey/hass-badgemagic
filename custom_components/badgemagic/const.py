"""Constants for the Badge Magic integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from badgemagic_ble import FONT_NAMES, Mode
from badgemagic_ble.const import LEGACY_SERVICE_UUID

DOMAIN: Final = "badgemagic"

SERVICE_UUID: Final = LEGACY_SERVICE_UUID

# Entry data, read from the badge once in the config flow.
CONF_MODEL: Final = "model"
CONF_FIRMWARE: Final = "firmware_revision"
CONF_HARDWARE: Final = "hardware_revision"
CONF_MANUFACTURER: Final = "manufacturer"
CONF_HAS_NEXTGEN: Final = "has_nextgen"

# Options: defaults for new messages.
CONF_FONT: Final = "font"
CONF_MODE: Final = "mode"
CONF_SPEED: Final = "speed"
CONF_FLASH: Final = "flash"
CONF_MARQUEE: Final = "marquee"
CONF_SET_CLOCK: Final = "set_clock"
# Stay connected after an upload so the open firmware keeps advertising.
CONF_KEEP_BLUETOOTH: Final = "keep_bluetooth_on"
# What to do with text wider than the display in modes that show 44-column pages.
CONF_WIDE_TEXT: Final = "wide_text"

# send_messages action fields.
ATTR_MESSAGES: Final = "messages"
ATTR_TEXT: Final = "text"
ATTR_IMAGE: Final = "image"
ATTR_PIN: Final = "pin"

ATTR_FRAME_HEIGHT: Final = "frame_height"
ATTR_SPEED_MS: Final = "speed_ms"
ATTR_ALIGN: Final = "align"
ATTR_EFFECT: Final = "effect"
ATTR_FPS: Final = "fps"
ATTR_DURATION: Final = "duration"
ATTR_STOP_AFTER: Final = "stop_after"

SERVICE_SEND_MESSAGES: Final = "send_messages"
SERVICE_SET_SPLASH_SCREEN: Final = "set_splash_screen"
SERVICE_STREAM_FRAME: Final = "stream_frame"
SERVICE_STREAM_TEXT: Final = "stream_text"

FONTS: Final = list(FONT_NAMES)
# Option values for the Mode select and the action, in firmware order.
MODES: Final = [mode.name.lower() for mode in Mode]

DEFAULT_FONT: Final = "normal"
DEFAULT_MODE: Final = "scroll_left"
DEFAULT_SPEED: Final = 4
DEFAULT_FLASH: Final = False
DEFAULT_MARQUEE: Final = False
DEFAULT_SET_CLOCK: Final = True
DEFAULT_KEEP_BLUETOOTH: Final = True

# Wide text in paged modes: "page" shows it 44 columns at a time (firmware
# behavior), "scroll" switches the message to scroll_left, "clip" keeps the
# first 44 columns.
WIDE_TEXT_PAGE: Final = "page"
WIDE_TEXT_SCROLL: Final = "scroll"
WIDE_TEXT_CLIP: Final = "clip"
WIDE_TEXT_OPTIONS: Final = [WIDE_TEXT_PAGE, WIDE_TEXT_SCROLL, WIDE_TEXT_CLIP]
DEFAULT_WIDE_TEXT: Final = WIDE_TEXT_PAGE
# Modes in which the firmware shows a wide bitmap as successive 44-column pages.
# "animation" is left out: there, pages are frames on purpose.
PAGED_MODES: Final = frozenset({"fixed", "snowflake", "picture", "laser"})

DEFAULT_OPTIONS: Final = {
    CONF_FONT: DEFAULT_FONT,
    CONF_MODE: DEFAULT_MODE,
    CONF_SPEED: DEFAULT_SPEED,
    CONF_FLASH: DEFAULT_FLASH,
    CONF_MARQUEE: DEFAULT_MARQUEE,
    CONF_SET_CLOCK: DEFAULT_SET_CLOCK,
    CONF_KEEP_BLUETOOTH: DEFAULT_KEEP_BLUETOOTH,
    CONF_WIDE_TEXT: DEFAULT_WIDE_TEXT,
}

MIN_SPEED: Final = 1
MAX_SPEED: Final = 8
MAX_MESSAGES: Final = 8
MAX_TEXT_LENGTH: Final = 255

BATTERY_INTERVAL: Final = timedelta(hours=1)

STORAGE_VERSION: Final = 1

DISPLAY_COLUMNS: Final = 44

# Brightness in percent. The open firmware lights the LEDs for 1..4 quarters of
# each scan cycle (levels 0..3); the OEM firmware takes the same four steps in
# the upload header.
BRIGHTNESS_STEPS: Final = (25, 50, 75, 100)

# Firmware defaults that load_fallback_cfg restores.
FIRMWARE_DEFAULT_NAME: Final = "LED Badge Magic"
FIRMWARE_DEFAULT_BRIGHTNESS: Final = 25

# A badge that stops advertising this soon after an upload gets a repair issue.
BLUETOOTH_OFF_ISSUE_WINDOW: Final = timedelta(minutes=10)

# Streaming.
MIN_FPS: Final = 1
MAX_FPS: Final = 20
DEFAULT_FPS: Final = 10
STREAM_RECONNECT_ATTEMPTS: Final = 3
STREAM_RECONNECT_DELAY: Final = 5.0
# The preview image follows a stream at most this often.
PREVIEW_STREAM_INTERVAL: Final = 1.0
EFFECT_AUTO: Final = "auto"
EFFECT_SCROLL: Final = "scroll"
EFFECT_FIXED: Final = "fixed"
EFFECTS: Final = [EFFECT_AUTO, EFFECT_SCROLL, EFFECT_FIXED]
ALIGNMENTS: Final = ["left", "center", "right"]
