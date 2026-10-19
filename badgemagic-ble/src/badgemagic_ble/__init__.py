"""Bluetooth LE client for LED badges running the FOSSASIA Badge Magic firmware."""

from importlib.metadata import PackageNotFoundError, version

from .exceptions import (
    BadgeConnectionError,
    BadgeError,
    BadgeNotFound,
    CommandError,
    CommandTimeout,
    ConfigWriteError,
    InvalidParameterError,
    NextGenUnsupported,
    NotStreamingError,
    PinRejected,
    PinRequired,
    ProtocolError,
    UnknownCommandError,
    UnknownSubcommandError,
    UploadError,
)
from .protocol.legacy import Message, Mode, OemBrightness, chunk16, encode_frame
from .render import FONT_NAMES, Bitmap, Xbm, get_font, load_image, parse_xbm, render_text

try:
    __version__ = version("badgemagic-ble")
except PackageNotFoundError:  # running from a source tree
    __version__ = "0.0.0"


def __getattr__(name: str):  # noqa: ANN202
    # The client pulls in bleak; import it only when asked for.
    if name in ("BadgeClient", "DeviceInfo"):
        from . import client

        return getattr(client, name)
    raise AttributeError(name)


__all__ = [
    "FONT_NAMES",
    "BadgeClient",
    "BadgeConnectionError",
    "BadgeError",
    "BadgeNotFound",
    "Bitmap",
    "CommandError",
    "CommandTimeout",
    "ConfigWriteError",
    "DeviceInfo",
    "InvalidParameterError",
    "Message",
    "Mode",
    "NextGenUnsupported",
    "NotStreamingError",
    "OemBrightness",
    "PinRejected",
    "PinRequired",
    "ProtocolError",
    "UnknownCommandError",
    "UnknownSubcommandError",
    "UploadError",
    "Xbm",
    "chunk16",
    "encode_frame",
    "get_font",
    "load_image",
    "parse_xbm",
    "render_text",
]
