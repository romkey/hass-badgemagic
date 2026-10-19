"""BadgeClient against a fake BleakClient (no hardware)."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError, BleakGATTProtocolError

from badgemagic_ble import client as client_module
from badgemagic_ble.client import BadgeClient, att_error_code
from badgemagic_ble.const import (
    BATTERY_LEVEL_CHAR_UUID,
    BATTERY_SERVICE_UUID,
    DEVICE_INFO_SERVICE_UUID,
    FIRMWARE_REV_CHAR_UUID,
    LEGACY_SERVICE_UUID,
    LEGACY_WRITE_CHAR_UUID,
    MANUFACTURER_NAME_CHAR_UUID,
    MODEL_NUMBER_CHAR_UUID,
    NG_NOTIFY_CHAR_UUID,
    NG_SERVICE_UUID,
    NG_WRITE_CHAR_UUID,
)
from badgemagic_ble.exceptions import (
    BadgeConnectionError,
    CommandTimeout,
    InvalidParameterError,
    NextGenUnsupported,
    NotStreamingError,
    PinRejected,
    PinRequired,
    UnknownCommandError,
)
from badgemagic_ble.protocol.legacy import Message, chunk16, encode_frame
from badgemagic_ble.render import Bitmap, render_text


class FakeServices:
    def __init__(self, services: set[str], chars: set[str]) -> None:
        self._services = services
        self._chars = chars

    def get_service(self, uuid: str) -> object | None:
        return object() if uuid in self._services else None

    def get_characteristic(self, uuid: str) -> object | None:
        return object() if uuid in self._chars else None


class FakeBleakClient:
    def __init__(self, nextgen: bool = True) -> None:
        services = {LEGACY_SERVICE_UUID, BATTERY_SERVICE_UUID, DEVICE_INFO_SERVICE_UUID}
        if nextgen:
            services.add(NG_SERVICE_UUID)
        self.reads: dict[str, bytes] = {
            BATTERY_LEVEL_CHAR_UUID: b"\x57",
            MODEL_NUMBER_CHAR_UUID: b"BM1144",
            FIRMWARE_REV_CHAR_UUID: b"v0.1-123-gb2137f0\x00",
            MANUFACTURER_NAME_CHAR_UUID: b"FOSSASIA",
        }
        self.services = FakeServices(services, set(self.reads))
        self.is_connected = True
        self.writes: list[tuple[str, bytes, bool]] = []
        self.notify_cb: Callable[[Any, bytearray], None] | None = None
        self.on_write: Callable[[str, bytes, int], None] = lambda uuid, data, index: None
        self.ng_result: Callable[[bytes], bytes | None] = lambda payload: b"\x00"

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool | None = None) -> None:
        index = len(self.writes)
        self.writes.append((uuid, bytes(data), bool(response)))
        self.on_write(uuid, bytes(data), index)
        if uuid == NG_WRITE_CHAR_UUID and self.notify_cb is not None:
            result = self.ng_result(bytes(data))
            if result is not None:
                # The firmware notifies before answering the write.
                self.notify_cb(None, bytearray(result))

    async def start_notify(self, uuid: str, callback: Callable[[Any, bytearray], None]) -> None:
        assert uuid == NG_NOTIFY_CHAR_UUID
        self.notify_cb = callback

    async def read_gatt_char(self, uuid: str) -> bytearray:
        return bytearray(self.reads[uuid])

    async def disconnect(self) -> None:
        self.is_connected = False


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeBleakClient:
    fake_client = FakeBleakClient()

    async def establish_connection(client_class, device, name, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["disconnected_callback"] is not None
        return fake_client

    monkeypatch.setattr(client_module, "establish_connection", establish_connection)
    return fake_client


DEVICE = BLEDevice("AA:BB:CC:DD:EE:FF", "LED Badge Magic", None)
NOW = dt.datetime(2026, 10, 18, 12, 0, 0)


async def test_upload_writes_16_byte_packets_with_response(fake: FakeBleakClient) -> None:
    messages = [Message(render_text("HELLO"))]
    async with BadgeClient(DEVICE) as badge:
        await badge.upload(messages, now=NOW)
    expected = chunk16(encode_frame(messages, NOW))
    assert [w[1] for w in fake.writes] == expected
    assert all(uuid == LEGACY_WRITE_CHAR_UUID and resp and len(data) == 16 for uuid, data, resp in fake.writes)
    assert not fake.is_connected


async def test_upload_without_clock_sends_zero_timestamp(fake: FakeBleakClient) -> None:
    async with BadgeClient(DEVICE) as badge:
        await badge.upload([Message(Bitmap(8))], set_clock=False)
    header = b"".join(w[1] for w in fake.writes[:4])
    assert header[0x26:0x2C] == bytes(6)


async def test_upload_pin_sent_first(fake: FakeBleakClient) -> None:
    async with BadgeClient(DEVICE) as badge:
        await badge.upload([Message(Bitmap(8))], pin="6512", now=NOW)
    assert fake.writes[0][1] == b"6512" + bytes(12)
    assert fake.writes[1][1].startswith(b"wang\x00")


async def test_att_0x0e_on_first_packet_is_pin_required(fake: FakeBleakClient) -> None:
    def reject(uuid: str, data: bytes, index: int) -> None:
        raise BleakGATTProtocolError(0x0E)

    fake.on_write = reject
    async with BadgeClient(DEVICE) as badge:
        with pytest.raises(PinRequired) as info:
            await badge.upload([Message(Bitmap(8))], now=NOW)
    assert not isinstance(info.value, PinRejected)


async def test_wrong_pin_is_pin_rejected(fake: FakeBleakClient) -> None:
    def reject_pin(uuid: str, data: bytes, index: int) -> None:
        if index == 0:
            raise BleakGATTProtocolError(0x0E)

    fake.on_write = reject_pin
    async with BadgeClient(DEVICE) as badge:
        with pytest.raises(PinRejected):
            await badge.upload([Message(Bitmap(8))], pin="0000", now=NOW)


async def test_nextgen_command_awaits_result(fake: FakeBleakClient) -> None:
    async with BadgeClient(DEVICE) as badge:
        assert badge.has_nextgen
        await badge.set_brightness(2)
        await badge.save_config()
    assert [w[1] for w in fake.writes] == [b"\x08\x01\x02", b"\x06"]
    assert all(w[0] == NG_WRITE_CHAR_UUID for w in fake.writes)


async def test_nextgen_error_result_raises(fake: FakeBleakClient) -> None:
    fake.ng_result = lambda payload: b"\xfe"
    async with BadgeClient(DEVICE) as badge:
        with pytest.raises(InvalidParameterError):
            await badge.set_brightness(3)


async def test_stream_frame_pads_and_reports_not_streaming(fake: FakeBleakClient) -> None:
    fake.ng_result = lambda payload: b"\xff" if payload[0] == 0x03 else b"\x00"
    bm = Bitmap(2)
    bm[0, 0] = True
    async with BadgeClient(DEVICE) as badge:
        with pytest.raises(NotStreamingError):
            await badge.stream_frame(bm)
    payload = fake.writes[0][1]
    assert len(payload) == 89
    # 2 columns centered in 44 -> 21 blank columns first.
    assert payload[1 + 2 * 21 : 1 + 2 * 21 + 2] == b"\x01\x00"


async def test_streaming_flag(fake: FakeBleakClient) -> None:
    async with BadgeClient(DEVICE) as badge:
        await badge.start_streaming()
        assert badge.is_streaming
        await badge.stream_frame(Bitmap(44), wait=False)
        await badge.stop_streaming()
        assert not badge.is_streaming
    assert [w[1][:2] for w in fake.writes] == [b"\x02\x00", b"\x03\x00", b"\x02\x01"]


async def test_nextgen_timeout(fake: FakeBleakClient) -> None:
    fake.ng_result = lambda payload: None
    async with BadgeClient(DEVICE, result_timeout=0.05) as badge:
        with pytest.raises(CommandTimeout):
            await badge.save_config()


async def test_unknown_command_att_error(fake: FakeBleakClient) -> None:
    def reject(uuid: str, data: bytes, index: int) -> None:
        raise BleakGATTProtocolError(0x18)

    fake.on_write = reject
    async with BadgeClient(DEVICE) as badge:
        with pytest.raises(UnknownCommandError):
            await badge.set_brightness(1)


async def test_power_off_tolerates_lost_response(fake: FakeBleakClient) -> None:
    def drop(uuid: str, data: bytes, index: int) -> None:
        raise BleakError("disconnected")

    fake.on_write = drop
    async with BadgeClient(DEVICE) as badge:
        await badge.power_off()
        assert not badge.is_connected
    assert fake.writes[0][1] == b"\x01\x00"


async def test_oem_badge_has_no_nextgen(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = FakeBleakClient(nextgen=False)

    async def establish_connection(*args, **kwargs):  # type: ignore[no-untyped-def]
        return fake_client

    monkeypatch.setattr(client_module, "establish_connection", establish_connection)
    async with BadgeClient(DEVICE) as badge:
        assert not badge.has_nextgen
        with pytest.raises(NextGenUnsupported):
            await badge.set_always_on(True)


async def test_battery_and_device_info(fake: FakeBleakClient) -> None:
    async with BadgeClient(DEVICE) as badge:
        assert await badge.read_battery() == 0x57
        info = await badge.read_device_info()
    assert info.model == "BM1144"
    assert info.firmware_revision == "v0.1-123-gb2137f0"
    assert info.manufacturer == "FOSSASIA"
    assert info.hardware_revision is None


async def test_disconnect_fails_pending_result(fake: FakeBleakClient) -> None:
    badge = BadgeClient(DEVICE, result_timeout=1)
    await badge.connect()
    fake.ng_result = lambda payload: None

    async def drop_soon() -> None:
        await asyncio.sleep(0.01)
        badge._on_disconnect(fake)  # type: ignore[arg-type]

    task = asyncio.create_task(drop_soon())
    with pytest.raises(Exception, match="disconnected"):
        await badge.save_config()
    await task


def test_att_error_code_parsing() -> None:
    assert att_error_code(BleakGATTProtocolError(0x0E)) == 0x0E
    assert att_error_code(BleakError("Operation failed with ATT error: 0x0e (Unlikely Error)")) == 0x0E
    assert att_error_code(BleakError("Error Domain=CBATTErrorDomain Code=14 \"Unlikely error.\"")) == 14
    assert att_error_code(BleakError("something else")) is None


async def test_upload_linger_keeps_connection_open(fake: FakeBleakClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        assert fake.is_connected  # still connected while lingering
        await real_sleep(0)

    monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
    async with BadgeClient(DEVICE) as badge:
        await badge.upload([Message(render_text("HI"))], now=NOW, linger=2.5)
    assert sleeps == [2.5]
    assert not fake.is_connected


async def test_upload_without_linger_does_not_sleep(fake: FakeBleakClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(delay: float) -> None:
        raise AssertionError("should not sleep")

    monkeypatch.setattr(client_module.asyncio, "sleep", no_sleep)
    async with BadgeClient(DEVICE) as badge:
        await badge.upload([Message(render_text("HI"))], now=NOW)


async def test_battery_read_error_is_wrapped(fake: FakeBleakClient) -> None:
    async def failing_read(uuid: str) -> bytearray:
        raise BleakError("read failed")

    fake.read_gatt_char = failing_read  # type: ignore[method-assign]
    async with BadgeClient(DEVICE) as badge:
        with pytest.raises(BadgeConnectionError, match="battery"):
            await badge.read_battery()
