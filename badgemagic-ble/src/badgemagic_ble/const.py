"""GATT UUIDs and limits of the Badge Magic firmware."""

from __future__ import annotations


def _uuid16(short: int) -> str:
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


# Legacy ("wang") upload profile, present on OEM and open firmware.
LEGACY_SERVICE_UUID = _uuid16(0xFEE0)
LEGACY_WRITE_CHAR_UUID = _uuid16(0xFEE1)

# Next-gen control profile, open firmware only.
NG_SERVICE_UUID = _uuid16(0xF055)
NG_NOTIFY_CHAR_UUID = _uuid16(0xF056)
NG_WRITE_CHAR_UUID = _uuid16(0xF057)

BATTERY_SERVICE_UUID = _uuid16(0x180F)
BATTERY_LEVEL_CHAR_UUID = _uuid16(0x2A19)

DEVICE_INFO_SERVICE_UUID = _uuid16(0x180A)
MODEL_NUMBER_CHAR_UUID = _uuid16(0x2A24)
SERIAL_NUMBER_CHAR_UUID = _uuid16(0x2A25)
FIRMWARE_REV_CHAR_UUID = _uuid16(0x2A26)
HARDWARE_REV_CHAR_UUID = _uuid16(0x2A27)
SOFTWARE_REV_CHAR_UUID = _uuid16(0x2A28)
MANUFACTURER_NAME_CHAR_UUID = _uuid16(0x2A29)

DISPLAY_ROWS = 11
DISPLAY_COLUMNS = 44

DEFAULT_NAME = "LED Badge Magic"
OEM_NAME = "LSLED"
MAX_NAME_LENGTH = 20

# ATT error the firmware returns for any rejected legacy packet, including
# a wrong PIN and a "wang" header sent before the PIN.
ATT_ERR_UNLIKELY = 0x0E
# Status the firmware returns as an ATT error for an unknown next-gen command.
ATT_ERR_INVALID_RANGE = 0x18
