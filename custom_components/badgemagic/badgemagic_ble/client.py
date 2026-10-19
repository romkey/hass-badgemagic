"""Asyncio BLE client for Badge Magic badges."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    ATT_ERR_INVALID_RANGE,
    ATT_ERR_UNLIKELY,
    BATTERY_LEVEL_CHAR_UUID,
    FIRMWARE_REV_CHAR_UUID,
    HARDWARE_REV_CHAR_UUID,
    LEGACY_WRITE_CHAR_UUID,
    MANUFACTURER_NAME_CHAR_UUID,
    MODEL_NUMBER_CHAR_UUID,
    NG_NOTIFY_CHAR_UUID,
    NG_SERVICE_UUID,
    NG_WRITE_CHAR_UUID,
    SERIAL_NUMBER_CHAR_UUID,
    SOFTWARE_REV_CHAR_UUID,
)
from .exceptions import (
    BadgeConnectionError,
    BadgeError,
    CommandTimeout,
    NextGenUnsupported,
    PinRejected,
    PinRequired,
    UnknownCommandError,
    UploadError,
)
from .protocol import legacy, ng
from .render import Bitmap, Xbm, fit_columns

_LOGGER = logging.getLogger(__name__)

DEFAULT_RESULT_TIMEOUT = 5.0
# The open firmware switches advertising off 1.5 s after an upload completes
# and back on whenever a central disconnects (src/main.c, handle_after_rx and
# src/ble/peripheral.c, link_onTerminated). Staying connected past the 1.5 s
# means the disconnect turns advertising back on.
KEEP_ADVERTISING_LINGER = 2.5
# power_off and reboot act before the firmware can answer the write.
_FIRE_AND_FORGET_TIMEOUT = 2.0


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Device Information service (0x180A); fields missing on the badge are None."""

    model: str | None = None
    firmware_revision: str | None = None
    hardware_revision: str | None = None
    software_revision: str | None = None
    serial_number: str | None = None
    manufacturer: str | None = None


_DEVICE_INFO_CHARS = {
    "model": MODEL_NUMBER_CHAR_UUID,
    "firmware_revision": FIRMWARE_REV_CHAR_UUID,
    "hardware_revision": HARDWARE_REV_CHAR_UUID,
    "software_revision": SOFTWARE_REV_CHAR_UUID,
    "serial_number": SERIAL_NUMBER_CHAR_UUID,
    "manufacturer": MANUFACTURER_NAME_CHAR_UUID,
}

_ATT_ERROR_PATTERNS = (
    re.compile(r"ATT error:? (0x[0-9a-f]+)", re.I),
    re.compile(r"CBATTErrorDomain Code=(\d+)", re.I),
    re.compile(r"\berror code[:=]? ?(0x[0-9a-f]+|\d+)", re.I),
)


def att_error_code(exc: BaseException) -> int | None:
    """The ATT error code carried by a bleak exception, if any."""
    args = getattr(exc, "args", ())
    if type(exc).__name__ == "BleakGATTProtocolError" and args and isinstance(args[0], int):
        return args[0]
    text = str(exc)
    for pattern in _ATT_ERROR_PATTERNS:
        if m := pattern.search(text):
            return int(m.group(1), 0)
    if "unlikely error" in text.lower():
        return ATT_ERR_UNLIKELY
    return None


class BadgeClient:
    """One badge, connected on demand.

    Use as an async context manager, or call ``connect()``/``disconnect()``.
    Each connection uses a fresh ``BleakClient`` from
    ``bleak_retry_connector.establish_connection``. All operations are
    serialized, since the badge accepts a single central and one command at
    a time.
    """

    def __init__(
        self,
        device: BLEDevice,
        *,
        result_timeout: float = DEFAULT_RESULT_TIMEOUT,
        max_attempts: int = 3,
        disconnected_callback: Callable[[BadgeClient], None] | None = None,
    ) -> None:
        self._device = device
        self._result_timeout = result_timeout
        self._max_attempts = max_attempts
        self._disconnected_callback = disconnected_callback
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._notify_enabled = False
        self._result: asyncio.Future[bytes] | None = None
        self._streaming = False

    # connection --------------------------------------------------------------

    @property
    def device(self) -> BLEDevice:
        return self._device

    @property
    def name(self) -> str:
        return self._device.name or self._device.address

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def is_streaming(self) -> bool:
        """Whether this client put the badge in streaming mode on this connection."""
        return self._streaming

    async def connect(self) -> None:
        if self.is_connected:
            return
        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                self._device,
                self.name,
                disconnected_callback=self._on_disconnect,
                max_attempts=self._max_attempts,
            )
        except (BleakError, TimeoutError) as err:
            raise BadgeConnectionError(f"could not connect to {self.name}: {err}") from err
        self._notify_enabled = False
        self._streaming = False

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._notify_enabled = False
        self._streaming = False
        if client is not None:
            with contextlib.suppress(BleakError, TimeoutError, EOFError):
                await client.disconnect()

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    def _on_disconnect(self, client: BleakClient) -> None:
        if client is not self._client:
            return
        _LOGGER.debug("%s disconnected", self.name)
        self._client = None
        self._notify_enabled = False
        self._streaming = False
        if self._result is not None and not self._result.done():
            self._result.set_exception(BadgeConnectionError("badge disconnected"))
        if self._disconnected_callback is not None:
            self._disconnected_callback(self)

    def _require_client(self) -> BleakClient:
        if self._client is None or not self._client.is_connected:
            raise BadgeConnectionError("not connected")
        return self._client

    @property
    def has_nextgen(self) -> bool:
        """Whether the connected badge exposes the next-gen service (open firmware)."""
        client = self._require_client()
        return client.services.get_service(NG_SERVICE_UUID) is not None

    # legacy upload -----------------------------------------------------------

    async def upload(
        self,
        messages: Sequence[legacy.Message],
        pin: str | int | None = None,
        set_clock: bool = True,
        brightness: legacy.OemBrightness | int | None = None,
        now: dt.datetime | None = None,
        linger: float = 0.0,
    ) -> None:
        """Upload 1..8 messages over the legacy profile.

        With ``set_clock`` the frame carries the current local time (or
        ``now``), which the open firmware loads into its clock. Without it the
        timestamp is zero; only OEM badges ignore that.

        ``linger`` keeps the connection open for that many seconds after the
        last packet. On the open firmware, lingering longer than 1.5 s (see
        ``KEEP_ADVERTISING_LINGER``) makes the badge advertise again after the
        disconnect instead of switching Bluetooth off. Unverified on hardware.

        Raises ``PinRequired`` if the badge has SECURITY enabled and no PIN was
        given, ``PinRejected`` for a wrong PIN.
        """
        timestamp = (now or dt.datetime.now()) if set_clock else None
        frame = legacy.encode_frame(messages, timestamp, brightness)
        packets = legacy.chunk16(frame)
        async with self._lock:
            client = self._require_client()
            if pin is not None:
                try:
                    await client.write_gatt_char(LEGACY_WRITE_CHAR_UUID, legacy.encode_pin(pin), response=True)
                except BleakError as err:
                    if att_error_code(err) == ATT_ERR_UNLIKELY:
                        raise PinRejected("the badge rejected the PIN") from err
                    raise BadgeConnectionError(f"PIN write failed: {err}") from err
            for index, packet in enumerate(packets):
                try:
                    await client.write_gatt_char(LEGACY_WRITE_CHAR_UUID, packet, response=True)
                except BleakError as err:
                    code = att_error_code(err)
                    if index == 0 and code == ATT_ERR_UNLIKELY:
                        if pin is None:
                            raise PinRequired(
                                "the badge requires a PIN; enter BT-PAIRING mode and pass the PIN it shows"
                            ) from err
                        raise PinRejected("the badge rejected the upload after the PIN") from err
                    if code is not None:
                        raise UploadError(f"packet {index + 1}/{len(packets)} rejected (ATT 0x{code:02x})") from err
                    raise BadgeConnectionError(f"packet {index + 1}/{len(packets)} failed: {err}") from err
            _LOGGER.debug("%s: uploaded %d bytes in %d packets", self.name, len(frame), len(packets))
            if linger > 0:
                await asyncio.sleep(linger)

    # next-gen ----------------------------------------------------------------

    def _on_result(self, _char: object, data: bytearray) -> None:
        if self._result is not None and not self._result.done():
            self._result.set_result(bytes(data))
        else:
            _LOGGER.debug("%s: unsolicited result %s", self.name, bytes(data).hex())

    async def _ensure_notify(self, client: BleakClient) -> None:
        if self._notify_enabled:
            return
        if client.services.get_service(NG_SERVICE_UUID) is None:
            raise NextGenUnsupported(f"{self.name} has no next-gen service (OEM or old firmware)")
        await client.start_notify(NG_NOTIFY_CHAR_UUID, self._on_result)
        self._notify_enabled = True

    async def _command(self, payload: bytes, *, wait: bool = True) -> None:
        command = ng.Command(payload[0])
        async with self._lock:
            client = self._require_client()
            await self._ensure_notify(client)
            loop = asyncio.get_running_loop()
            self._result = loop.create_future() if wait else None
            try:
                await client.write_gatt_char(NG_WRITE_CHAR_UUID, payload, response=True)
            except BleakError as err:
                code = att_error_code(err)
                if code == ATT_ERR_INVALID_RANGE:
                    raise UnknownCommandError(f"firmware does not support command 0x{command:02x}") from err
                raise BadgeError(f"command 0x{command:02x} write failed: {err}") from err
            if self._result is None:
                return
            try:
                async with asyncio.timeout(self._result_timeout):
                    data = await self._result
            except TimeoutError as err:
                raise CommandTimeout(f"no result for command 0x{command:02x}") from err
            finally:
                self._result = None
            ng.decode_result(command, data)

    async def _fire_and_forget(self, payload: bytes) -> None:
        """Send a command after which the badge goes away (power off, reboot)."""
        async with self._lock:
            client = self._require_client()
            if client.services.get_service(NG_SERVICE_UUID) is None:
                raise NextGenUnsupported(f"{self.name} has no next-gen service")
            with contextlib.suppress(BleakError, TimeoutError, EOFError):
                async with asyncio.timeout(_FIRE_AND_FORGET_TIMEOUT):
                    await client.write_gatt_char(NG_WRITE_CHAR_UUID, payload, response=True)
        await self.disconnect()

    async def start_streaming(self) -> None:
        await self._command(ng.encode_start_streaming())
        self._streaming = True

    async def stop_streaming(self) -> None:
        await self._command(ng.encode_stop_streaming())
        self._streaming = False

    async def stream_frame(self, frame: Bitmap | bytes, *, wait: bool = True) -> None:
        """Show one frame while streaming.

        A ``Bitmap`` is padded (centered) or clipped to 44 columns; ``bytes``
        are raw uint16 columns. With ``wait=False`` the result notification
        is not awaited, which raises the achievable frame rate.
        """
        columns = fit_columns(frame).to_columns() if isinstance(frame, Bitmap) else frame
        await self._command(ng.encode_stream_bitmap(columns), wait=wait)

    async def set_brightness(self, level: int) -> None:
        await self._command(ng.encode_set_brightness(level))

    async def set_always_on(self, enabled: bool) -> None:
        await self._command(ng.encode_set_always_on(enabled))

    async def set_name(self, name: str) -> None:
        await self._command(ng.encode_set_name(name))

    async def set_reset_after_upload(self, enabled: bool) -> None:
        await self._command(ng.encode_set_reset_after_upload(enabled))

    async def set_splash(self, xbm: Xbm, frame_height: int = 11) -> None:
        await self._command(ng.encode_set_splash(xbm.width, xbm.height, frame_height, xbm.bits))

    async def set_splash_speed(self, ms: int) -> None:
        await self._command(ng.encode_set_splash_speed(ms))

    async def save_config(self) -> None:
        await self._command(ng.encode_save_config())

    async def load_defaults(self) -> None:
        await self._command(ng.encode_load_defaults())

    async def power_off(self) -> None:
        await self._fire_and_forget(ng.encode_power_off())

    async def reboot(self) -> None:
        await self._fire_and_forget(ng.encode_reboot())

    # standard services -------------------------------------------------------

    async def read_battery(self) -> int:
        """Battery level in percent."""
        async with self._lock:
            client = self._require_client()
            try:
                data = await client.read_gatt_char(BATTERY_LEVEL_CHAR_UUID)
            except BleakError as err:
                raise BadgeConnectionError(f"reading the battery level failed: {err}") from err
        if not data:
            raise BadgeError("empty battery level")
        return data[0]

    async def read_device_info(self) -> DeviceInfo:
        values: dict[str, str | None] = {}
        async with self._lock:
            client = self._require_client()
            for field, uuid in _DEVICE_INFO_CHARS.items():
                if client.services.get_characteristic(uuid) is None:
                    values[field] = None
                    continue
                try:
                    raw = await client.read_gatt_char(uuid)
                except BleakError as err:
                    _LOGGER.debug("%s: reading %s failed: %s", self.name, field, err)
                    values[field] = None
                    continue
                values[field] = bytes(raw).split(b"\x00", 1)[0].decode("utf-8", "replace").strip() or None
        return DeviceInfo(**values)
