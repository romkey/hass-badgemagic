"""Next-gen command encoders and result decoding."""

from __future__ import annotations

import pytest

from badgemagic_ble.exceptions import (
    CommandError,
    ConfigWriteError,
    InvalidParameterError,
    NotStreamingError,
    ProtocolError,
    UnknownSubcommandError,
)
from badgemagic_ble.protocol import ng
from badgemagic_ble.protocol.ng import Command


def test_command_codes() -> None:
    assert [int(c) for c in Command] == list(range(9))


def test_power_setting() -> None:
    assert ng.encode_power_off() == bytes([0x01, 0x00])
    assert ng.encode_set_reset_after_upload(True) == bytes([0x01, 0x01, 0x01])
    assert ng.encode_set_reset_after_upload(False) == bytes([0x01, 0x01, 0x00])
    assert ng.encode_reboot() == bytes([0x01, 0x02])


def test_streaming_setting() -> None:
    assert ng.encode_start_streaming() == bytes([0x02, 0x00])
    assert ng.encode_stop_streaming() == bytes([0x02, 0x01])


def test_stream_bitmap_pads_to_44_columns() -> None:
    payload = ng.encode_stream_bitmap(bytes([0xFF, 0x07, 0x01, 0x00]))
    assert len(payload) == 1 + 88
    assert payload[:5] == bytes([0x03, 0xFF, 0x07, 0x01, 0x00])
    assert payload[5:] == bytes(84)
    full = bytes(range(88))
    assert ng.encode_stream_bitmap(full) == b"\x03" + full


def test_stream_bitmap_limits() -> None:
    with pytest.raises(ValueError):
        ng.encode_stream_bitmap(bytes(89))
    with pytest.raises(ValueError):
        ng.encode_stream_bitmap(bytes(3))


def test_ble_setting() -> None:
    assert ng.encode_set_always_on(True) == bytes([0x04, 0x00, 0x01])
    assert ng.encode_set_always_on(False) == bytes([0x04, 0x00, 0x00])
    assert ng.encode_set_name("Kitchen") == b"\x04\x01Kitchen"
    assert ng.encode_set_name("x" * 20) == b"\x04\x01" + b"x" * 20
    with pytest.raises(ValueError):
        ng.encode_set_name("x" * 21)
    with pytest.raises(ValueError):
        ng.encode_set_name("")
    with pytest.raises(ValueError):
        ng.encode_set_name("é" * 11)  # 22 bytes of UTF-8


def test_flash_splash_screen() -> None:
    bits = bytes(range(6 * 11))
    assert ng.encode_set_splash(44, 11, 11, bits) == bytes([0x05, 44, 11, 11]) + bits
    assert ng.encode_set_splash(1, 1, 1, b"\x01") == bytes([0x05, 1, 1, 1, 1])
    assert len(ng.encode_set_splash(48, 42, 11, bytes(252))) == 4 + 252


def test_flash_splash_screen_limits() -> None:
    with pytest.raises(ValueError):
        ng.encode_set_splash(49, 11, 11, bytes(7 * 11))
    with pytest.raises(ValueError):
        ng.encode_set_splash(8, 45, 11, bytes(45))
    with pytest.raises(ValueError):
        ng.encode_set_splash(44, 11, 11, bytes(10))  # wrong length
    with pytest.raises(ValueError):
        ng.encode_set_splash(48, 44, 11, bytes(264))  # firmware uint8 length wraps
    with pytest.raises(ValueError):
        ng.encode_set_splash(8, 8, 0, bytes(8))


def test_config_commands() -> None:
    assert ng.encode_save_config() == bytes([0x06])
    assert ng.encode_load_defaults() == bytes([0x07])
    assert ng.encode_next_packet() == bytes([0x00])


def test_misc() -> None:
    assert ng.encode_set_splash_speed(30) == bytes([0x08, 0x00, 30, 0])
    assert ng.encode_set_splash_speed(0x1234) == bytes([0x08, 0x00, 0x34, 0x12])  # little-endian
    for level in range(4):
        assert ng.encode_set_brightness(level) == bytes([0x08, 0x01, level])
    for bad in (-1, 4):
        with pytest.raises(ValueError):
            ng.encode_set_brightness(bad)
    for bad in (-1, 65536):
        with pytest.raises(ValueError):
            ng.encode_set_splash_speed(bad)


def test_decode_success() -> None:
    for cmd in Command:
        ng.decode_result(cmd, b"\x00")


def test_decode_malformed() -> None:
    with pytest.raises(ProtocolError):
        ng.decode_result(Command.SAVE_CFG, b"")
    with pytest.raises(ProtocolError):
        ng.decode_result(Command.SAVE_CFG, b"\x00\x00")


@pytest.mark.parametrize(
    ("command", "code", "exc_type"),
    [
        (Command.POWER_SETTING, 0xFF, UnknownSubcommandError),
        (Command.POWER_SETTING, 0xFE, InvalidParameterError),
        (Command.STREAM_BITMAP, 0xFF, NotStreamingError),
        (Command.BLE_SETTING, 0xFF, UnknownSubcommandError),
        (Command.BLE_SETTING, 0xFE, InvalidParameterError),
        (Command.FLASH_SPLASH_SCREEN, 0xFF, InvalidParameterError),
        (Command.FLASH_SPLASH_SCREEN, 0xFE, InvalidParameterError),
        (Command.FLASH_SPLASH_SCREEN, 0xFD, InvalidParameterError),
        (Command.FLASH_SPLASH_SCREEN, 0xFC, InvalidParameterError),
        (Command.SAVE_CFG, 0x01, ConfigWriteError),
        (Command.SAVE_CFG, 0xFF, ConfigWriteError),
        (Command.MISC, 0xFF, UnknownSubcommandError),
        (Command.MISC, 0xFE, InvalidParameterError),
        (Command.STREAMING_SETTING, 0x42, CommandError),
    ],
)
def test_decode_errors(command: Command, code: int, exc_type: type[CommandError]) -> None:
    with pytest.raises(exc_type) as info:
        ng.decode_result(command, bytes([code]))
    assert info.value.command == command
    assert info.value.code == code
    assert isinstance(info.value, CommandError)


def test_decode_unknown_command_number() -> None:
    with pytest.raises(CommandError) as info:
        ng.decode_result(0x42, b"\x01")
    assert info.value.command == 0x42
