"""Exceptions raised by badgemagic-ble."""

from __future__ import annotations


class BadgeError(Exception):
    """Base class for all badge errors."""


class BadgeConnectionError(BadgeError):
    """The badge could not be reached or the connection dropped."""


class BadgeNotFound(BadgeConnectionError):
    """No advertising badge matched the requested address or name."""


class ProtocolError(BadgeError):
    """The badge sent something the protocol does not allow."""


class UploadError(BadgeError):
    """The badge rejected a legacy upload packet."""


class PinRequired(UploadError):
    """The badge has SECURITY enabled and needs the PIN shown in BT-PAIRING mode."""


class PinRejected(PinRequired):
    """The badge rejected the PIN that was sent."""


class NextGenUnsupported(BadgeError):
    """The badge has no next-gen service (OEM firmware or very old open firmware)."""


class CommandTimeout(BadgeError):
    """No result notification arrived for a next-gen command."""


class UnknownCommandError(BadgeError):
    """The firmware does not know this next-gen command (older firmware)."""


class CommandError(BadgeError):
    """A next-gen command returned a non-zero result code."""

    def __init__(self, command: int, code: int, message: str | None = None) -> None:
        self.command = command
        self.code = code
        text = message or "command failed"
        super().__init__(f"{text} (command 0x{command:02x}, result 0x{code:02x})")


class InvalidParameterError(CommandError):
    """A parameter was out of range or malformed."""


class UnknownSubcommandError(InvalidParameterError):
    """The sub-command byte (first parameter) is not known to the firmware."""


class NotStreamingError(CommandError):
    """stream_bitmap was sent while the badge was not in streaming mode."""


class ConfigWriteError(CommandError):
    """save_cfg failed to write the configuration to flash."""
