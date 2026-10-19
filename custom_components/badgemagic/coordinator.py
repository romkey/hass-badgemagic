"""Connection handling and remembered state for one badge."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .badgemagic_ble import (
    BadgeClient,
    BadgeError,
    Bitmap,
    Message,
    Mode,
    OemBrightness,
    PinRejected,
    PinRequired,
    Xbm,
    encode_frame,
    render_text,
)
from .badgemagic_ble.client import KEEP_ADVERTISING_LINGER
from .badgemagic_ble.render import fit_columns
from .const import (
    BATTERY_INTERVAL,
    BLUETOOTH_OFF_ISSUE_WINDOW,
    BRIGHTNESS_STEPS,
    CONF_FLASH,
    CONF_FONT,
    CONF_HAS_NEXTGEN,
    CONF_KEEP_BLUETOOTH,
    CONF_MARQUEE,
    CONF_MODE,
    CONF_SET_CLOCK,
    CONF_SPEED,
    CONF_WIDE_TEXT,
    DEFAULT_OPTIONS,
    DISPLAY_COLUMNS,
    DOMAIN,
    EFFECT_AUTO,
    EFFECT_SCROLL,
    FIRMWARE_DEFAULT_BRIGHTNESS,
    FIRMWARE_DEFAULT_NAME,
    PAGED_MODES,
    PREVIEW_STREAM_INTERVAL,
    STORAGE_VERSION,
    STREAM_RECONNECT_ATTEMPTS,
    STREAM_RECONNECT_DELAY,
    WIDE_TEXT_CLIP,
    WIDE_TEXT_SCROLL,
)

_LOGGER = logging.getLogger(__name__)

type BadgeConfigEntry = ConfigEntry[BadgeCoordinator]

# Indirection so tests can run streams without waiting in real time.
_sleep = asyncio.sleep
_monotonic = time.monotonic

_OEM_BRIGHTNESS = {
    25: OemBrightness.QUARTER,
    50: OemBrightness.HALF,
    75: OemBrightness.THREE_QUARTERS,
    100: OemBrightness.FULL,
}


@dataclass(frozen=True, slots=True)
class BadgeState:
    """What Home Assistant last sent to the badge; the badge cannot be read back."""

    text: str | None
    mode: str
    speed: int
    flash: bool
    marquee: bool


@dataclass(frozen=True, slots=True)
class BadgeSettings:
    """Badge configuration Home Assistant last set; None means never set from HA."""

    brightness: int | None = None  # percent, one of BRIGHTNESS_STEPS
    always_on: bool | None = None
    reset_after_upload: bool | None = None
    device_name: str | None = None


def mode_from_name(name: str) -> Mode:
    """Mode enum member for an option value such as ``scroll_left``."""
    return Mode[name.upper()]


def brightness_level(percent: int) -> int:
    """Open-firmware brightness level 0..3 for 25..100 percent."""
    return BRIGHTNESS_STEPS.index(percent)


def scroll_frames(bitmap: Bitmap) -> list[Bitmap]:
    """Frames that move ``bitmap`` in from the right edge and out past the left."""
    return [bitmap.crop(x, DISPLAY_COLUMNS) for x in range(-DISPLAY_COLUMNS, bitmap.width + 1)]


class BadgeCoordinator:
    """Owns one badge: desired state, the BLE lock, availability, battery and streaming.

    All BLE work goes through one lock because the badge accepts a single
    central. Outside streaming, a fresh connection is made for every
    operation and closed afterwards, so the badge is free for the phone app
    in between. While streaming, one connection is held open and every
    operation uses it.
    """

    def __init__(self, hass: HomeAssistant, entry: BadgeConfigEntry, address: str) -> None:
        self.hass = hass
        self.entry = entry
        self.address = address
        self.state = BadgeState(
            text=None,
            mode=self.option(CONF_MODE),
            speed=self.option(CONF_SPEED),
            flash=self.option(CONF_FLASH),
            marquee=self.option(CONF_MARQUEE),
        )
        self.settings = BadgeSettings()
        self.battery: int | None = None
        self.battery_updated: datetime | None = None
        self.reachable = False
        self.last_upload: dict[str, Any] | None = None
        self.last_upload_time: datetime | None = None
        self.last_error: str | None = None
        self.stats = {"connections": 0, "connection_failures": 0, "uploads": 0, "frames": 0, "stream_drops": 0}
        self.streaming = False
        self.preview: Bitmap | None = None
        self.preview_updated: datetime | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._preview_listeners: list[CALLBACK_TYPE] = []
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._battery_task: asyncio.Task[None] | None = None
        self._stream_client: BadgeClient | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._last_frame: Bitmap | None = None
        self._preview_notified = 0.0

    def option(self, key: str) -> Any:
        """A per-badge default from the options flow."""
        return self.entry.options.get(key, DEFAULT_OPTIONS[key])

    @property
    def name(self) -> str:
        return self.entry.title

    @property
    def has_nextgen(self) -> bool:
        """Whether the badge runs the open firmware with the next-gen service."""
        return bool(self.entry.data.get(CONF_HAS_NEXTGEN))

    @property
    def issue_id(self) -> str:
        return f"bluetooth_off_{self.entry.entry_id}"

    # lifecycle ---------------------------------------------------------------

    async def async_start(self) -> None:
        """Restore remembered state and start tracking the badge's advertisements."""
        if stored := await self._store.async_load():
            fields = {k: v for k, v in stored.get("state", {}).items() if k in BadgeState.__slots__}
            self.state = replace(self.state, **fields)
            settings = {k: v for k, v in stored.get("settings", {}).items() if k in BadgeSettings.__slots__}
            self.settings = replace(self.settings, **settings)
            self.battery = stored.get("battery")
            if updated := stored.get("battery_updated"):
                self.battery_updated = dt_util.parse_datetime(updated)

        self.reachable = bluetooth.async_address_present(self.hass, self.address, connectable=True)
        entry = self.entry
        entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._async_on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        entry.async_on_unload(
            bluetooth.async_track_unavailable(self.hass, self._async_on_unavailable, self.address, connectable=True)
        )
        entry.async_on_unload(async_track_time_interval(self.hass, self._async_battery_interval, BATTERY_INTERVAL))
        if self.reachable:
            self._async_schedule_battery_refresh()

    async def async_shutdown(self) -> None:
        """Leave streaming and close any open connection (config entry unload)."""
        await self.async_stop_streaming(notify=False)

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def async_add_preview_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Called when the preview bitmap changes (throttled while streaming)."""
        self._preview_listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._preview_listeners.remove(update_callback)

        return remove

    @callback
    def _async_notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    @callback
    def _async_on_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
    ) -> None:
        if not self.reachable:
            _LOGGER.debug("%s is advertising", self.name)
            self.reachable = True
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            self._async_notify()
        if self._battery_stale():
            self._async_schedule_battery_refresh()

    @callback
    def _async_on_unavailable(self, service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        _LOGGER.debug("%s stopped advertising", self.name)
        self.reachable = False
        if (
            self.last_upload_time is not None
            and dt_util.utcnow() - self.last_upload_time <= BLUETOOTH_OFF_ISSUE_WINDOW
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="bluetooth_off_after_upload",
                translation_placeholders={"name": self.name},
            )
        self._async_notify()

    @callback
    def _async_battery_interval(self, _now: datetime) -> None:
        if bluetooth.async_address_present(self.hass, self.address, connectable=True):
            self._async_schedule_battery_refresh()

    def _battery_stale(self) -> bool:
        return self.battery_updated is None or dt_util.utcnow() - self.battery_updated >= BATTERY_INTERVAL

    @callback
    def _async_schedule_battery_refresh(self) -> None:
        if self._battery_task is not None and not self._battery_task.done():
            return
        if self._lock.locked() or self.streaming:
            # A connection is in progress (or held) and reads the battery anyway.
            return
        self._battery_task = self.entry.async_create_background_task(
            self.hass, self._async_refresh_battery_quietly(), f"{DOMAIN} battery {self.address}"
        )

    async def _async_refresh_battery_quietly(self) -> None:
        try:
            await self.async_refresh_battery()
        except HomeAssistantError as err:
            _LOGGER.debug("%s: battery refresh failed: %s", self.name, err)

    # BLE ---------------------------------------------------------------------

    def _ble_device(self) -> Any:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            self.last_error = "not advertising"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_advertising",
                translation_placeholders={"name": self.name},
            )
        return device

    async def _async_open_client(self, **kwargs: Any) -> BadgeClient:
        client = BadgeClient(self._ble_device(), **kwargs)
        try:
            await client.connect()
        except BadgeError as err:
            self.stats["connection_failures"] += 1
            self.last_error = str(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"name": self.name, "error": str(err)},
            ) from err
        self.stats["connections"] += 1
        return client

    @asynccontextmanager
    async def _async_session(self) -> AsyncIterator[BadgeClient]:
        """A connected client; the caller must hold the lock.

        Uses the streaming connection when one is open, otherwise connects
        and disconnects around the block.
        """
        if self._stream_client is not None and self._stream_client.is_connected:
            yield self._stream_client
            return
        client = await self._async_open_client()
        try:
            yield client
        finally:
            await client.disconnect()

    async def _async_read_battery(self, client: BadgeClient) -> None:
        try:
            level = await client.read_battery()
        except (BadgeError, BleakError, TimeoutError) as err:
            _LOGGER.debug("%s: reading battery failed: %s", self.name, err)
            return
        self.battery = level
        self.battery_updated = dt_util.utcnow()
        self._async_save()
        self._async_notify()

    async def async_refresh_battery(self) -> None:
        async with self._lock, self._async_session() as client:
            await self._async_read_battery(client)

    async def async_upload(
        self,
        messages: Sequence[Message],
        *,
        pin: str | None = None,
        set_clock: bool | None = None,
    ) -> None:
        """Upload 1..8 messages, replacing everything stored on the badge.

        Leaves streaming first, since the upload replaces what is shown.
        """
        if set_clock is None:
            set_clock = self.option(CONF_SET_CLOCK)
        if self.streaming or self._stream_client is not None:
            await self.async_stop_streaming()
        linger = KEEP_ADVERTISING_LINGER if self.has_nextgen and self.option(CONF_KEEP_BLUETOOTH) else 0.0
        brightness = None
        if not self.has_nextgen and self.settings.brightness is not None:
            brightness = _OEM_BRIGHTNESS[self.settings.brightness]
        now = dt_util.now()
        async with self._lock:
            async with self._async_session() as client:
                # Read the battery first: open firmware switches Bluetooth
                # off right after an upload.
                await self._async_read_battery(client)
                try:
                    await client.upload(
                        messages, pin=pin, set_clock=set_clock, brightness=brightness, now=now, linger=linger
                    )
                except PinRejected as err:
                    self.last_error = str(err)
                    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pin_rejected") from err
                except PinRequired as err:
                    self.last_error = str(err)
                    raise HomeAssistantError(translation_domain=DOMAIN, translation_key="pin_required") from err
                except BadgeError as err:
                    self.last_error = str(err)
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="upload_failed",
                        translation_placeholders={"name": self.name, "error": str(err)},
                    ) from err
            self.stats["uploads"] += 1
            self.last_error = None
            self.last_upload_time = dt_util.utcnow()
            frame = encode_frame(messages, now if set_clock else None, brightness)
            self.last_upload = {
                "time": now.isoformat(),
                "messages": len(messages),
                "set_clock": set_clock,
                "pin": pin is not None,
                "linger": linger,
                "frame_bytes": len(frame),
                "frame": frame.hex(),
            }
        self._async_set_preview(messages[0].bitmap, force=True)
        _LOGGER.debug("%s: uploaded %d message(s)", self.name, len(messages))

    # messages ----------------------------------------------------------------

    async def async_render_text(self, text: str, font: str | None = None) -> Bitmap:
        """Render text off the event loop (the first use of a font reads its file)."""
        # An empty message is sent as one blank character, which clears the display.
        return await self.hass.async_add_executor_job(render_text, text or " ", font or self.option(CONF_FONT))

    async def async_text_message(
        self,
        text: str,
        *,
        mode: str,
        speed: int,
        flash: bool,
        marquee: bool,
        font: str | None = None,
    ) -> Message:
        """A text message, with the wide-text option applied in paged modes."""
        bitmap = await self.async_render_text(text, font)
        if mode in PAGED_MODES and bitmap.width > DISPLAY_COLUMNS:
            wide = self.option(CONF_WIDE_TEXT)
            if wide == WIDE_TEXT_SCROLL:
                mode = "scroll_left"
            elif wide == WIDE_TEXT_CLIP:
                _LOGGER.warning(
                    "%s: text is %d columns wide; showing the first %d in %s mode",
                    self.name,
                    bitmap.width,
                    DISPLAY_COLUMNS,
                    mode,
                )
                bitmap = bitmap.crop(0, DISPLAY_COLUMNS)
        return Message(bitmap, mode=mode_from_name(mode), speed=speed, flash=flash, marquee=marquee)

    async def _async_upload_state(self, state: BadgeState) -> None:
        assert state.text is not None
        message = await self.async_text_message(
            state.text, mode=state.mode, speed=state.speed, flash=state.flash, marquee=state.marquee
        )
        await self.async_upload([message])

    async def async_send_text(self, text: str) -> None:
        """Upload ``text`` as message 1 with the current mode, speed, flash and marquee."""
        new_state = replace(self.state, text=text)
        await self._async_upload_state(new_state)
        self._async_set_state(new_state)

    async def async_update_settings(self, **changes: Any) -> None:
        """Change mode/speed/flash/marquee and re-upload the current message.

        If the upload fails the change is not kept, so the entities keep
        showing what is on the badge.
        """
        new_state = replace(self.state, **changes)
        if new_state.text is not None:
            await self._async_upload_state(new_state)
        self._async_set_state(new_state)

    # next-gen configuration --------------------------------------------------

    def _require_nextgen(self) -> None:
        if not self.has_nextgen:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="nextgen_required",
                translation_placeholders={"name": self.name},
            )

    async def _async_command(self, action: Callable[[BadgeClient], Awaitable[None]], *, save: bool) -> None:
        """Run next-gen commands on one connection, then save_cfg if asked."""
        self._require_nextgen()
        async with self._lock, self._async_session() as client:
            if client is not self._stream_client and self._battery_stale():
                await self._async_read_battery(client)
            try:
                await action(client)
                if save:
                    await client.save_config()
            except BadgeError as err:
                self.last_error = str(err)
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="command_failed",
                    translation_placeholders={"name": self.name, "error": str(err)},
                ) from err
        self.last_error = None

    async def async_set_brightness(self, percent: int) -> None:
        """Brightness 25/50/75/100 %.

        Open firmware: applied now and saved. OEM firmware: sent in the upload
        header, so the current message is re-uploaded.
        """
        if percent not in BRIGHTNESS_STEPS:
            raise HomeAssistantError(f"brightness must be one of {BRIGHTNESS_STEPS}")
        if self.has_nextgen:
            level = brightness_level(percent)
            await self._async_command(lambda client: client.set_brightness(level), save=True)
            self._async_set_settings(replace(self.settings, brightness=percent))
            return
        old = self.settings
        self.settings = replace(old, brightness=percent)
        try:
            if self.state.text is not None:
                await self._async_upload_state(self.state)
        except HomeAssistantError:
            self.settings = old
            raise
        self._async_set_settings(self.settings)

    async def async_set_always_on(self, enabled: bool) -> None:
        """Keep Bluetooth advertising in every mode. Takes effect when the badge next starts."""
        await self._async_command(lambda client: client.set_always_on(enabled), save=True)
        self._async_set_settings(replace(self.settings, always_on=enabled))

    async def async_set_reset_after_upload(self, enabled: bool) -> None:
        """Reboot after each upload instead of showing BLUETOOTH OFF."""
        await self._async_command(lambda client: client.set_reset_after_upload(enabled), save=True)
        self._async_set_settings(replace(self.settings, reset_after_upload=enabled))

    async def async_set_device_name(self, name: str) -> None:
        """Rename the badge's Bluetooth name. It advertises the new name after it restarts."""
        name = name.strip()
        await self._async_command(lambda client: client.set_name(name), save=True)
        self._async_set_settings(replace(self.settings, device_name=name))
        self._async_rename(name)

    @callback
    def _async_rename(self, name: str) -> None:
        dev_reg = dr.async_get(self.hass)
        if device := dev_reg.async_get_device_by_identifier((DOMAIN, self.address), self.entry.entry_id):
            dev_reg.async_update_device(device.id, name=name)
        self.hass.config_entries.async_update_entry(self.entry, title=name)

    async def async_set_splash(self, xbm: Xbm, frame_height: int, speed_ms: int | None) -> None:
        """Replace the boot splash image (and its frame delay), then save."""

        async def action(client: BadgeClient) -> None:
            await client.set_splash(xbm, frame_height)
            if speed_ms is not None:
                await client.set_splash_speed(speed_ms)

        await self._async_command(action, save=True)

    async def async_save_config(self) -> None:
        await self._async_command(lambda client: client.save_config(), save=False)

    async def async_load_defaults(self) -> None:
        """Restore the firmware's default settings and save them."""
        await self._async_command(lambda client: client.load_defaults(), save=True)
        self._async_set_settings(
            BadgeSettings(
                brightness=FIRMWARE_DEFAULT_BRIGHTNESS,
                always_on=False,
                reset_after_upload=False,
                device_name=FIRMWARE_DEFAULT_NAME,
            )
        )

    async def async_power_off(self) -> None:
        self._require_nextgen()
        await self.async_stop_streaming()
        await self._async_command(lambda client: client.power_off(), save=False)

    async def async_reboot(self) -> None:
        self._require_nextgen()
        await self.async_stop_streaming()
        await self._async_command(lambda client: client.reboot(), save=False)

    # streaming ---------------------------------------------------------------

    async def _async_open_stream(self) -> None:
        """Connect and enter streaming mode; the caller must hold the lock."""
        if self._stream_client is not None and self._stream_client.is_connected:
            return
        client = await self._async_open_client(disconnected_callback=self._on_stream_disconnect)
        try:
            await client.start_streaming()
        except BadgeError as err:
            await client.disconnect()
            self.last_error = str(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"name": self.name, "error": str(err)},
            ) from err
        self._stream_client = client

    async def async_start_streaming(self) -> None:
        """Hold a connection open with the badge in streaming mode."""
        self._require_nextgen()
        async with self._lock:
            await self._async_open_stream()
        self.streaming = True
        self._async_notify()

    async def async_stop_streaming(self, *, notify: bool = True) -> None:
        """Leave streaming mode, which resumes the badge's own display, and disconnect."""
        await self._async_cancel_task("_stream_task")
        await self._async_cancel_task("_reconnect_task")
        was_streaming = self.streaming
        self.streaming = False
        async with self._lock:
            client, self._stream_client = self._stream_client, None
            if client is not None:
                if client.is_connected:
                    with contextlib.suppress(BadgeError, BleakError, TimeoutError):
                        await client.stop_streaming()
                await client.disconnect()
        if notify and (was_streaming or client is not None):
            self._async_notify()

    async def _async_cancel_task(self, attr: str) -> None:
        task: asyncio.Task[None] | None = getattr(self, attr)
        setattr(self, attr, None)
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _on_stream_disconnect(self, client: BadgeClient) -> None:
        # bleak may call this from another thread on some backends.
        self.hass.loop.call_soon_threadsafe(self._async_on_stream_drop, client)

    @callback
    def _async_on_stream_drop(self, client: BadgeClient) -> None:
        if client is not self._stream_client:
            return  # a connection we closed on purpose
        self._stream_client = None
        if not self.streaming:
            return
        self.stats["stream_drops"] += 1
        _LOGGER.info("%s: streaming connection dropped; reconnecting", self.name)
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.entry.async_create_background_task(
                self.hass, self._async_reconnect_stream(), f"{DOMAIN} stream reconnect {self.address}"
            )

    async def _async_reconnect_stream(self) -> None:
        for attempt in range(1, STREAM_RECONNECT_ATTEMPTS + 1):
            await _sleep(STREAM_RECONNECT_DELAY)
            if not self.streaming:
                return
            try:
                async with self._lock:
                    await self._async_open_stream()
                    if self._last_frame is not None and self._stream_client is not None:
                        await self._stream_client.stream_frame(self._last_frame)
            except (HomeAssistantError, BadgeError) as err:
                _LOGGER.debug("%s: stream reconnect %d failed: %s", self.name, attempt, err)
                continue
            _LOGGER.info("%s: streaming connection restored", self.name)
            return
        _LOGGER.warning("%s: could not restore the streaming connection; streaming stopped", self.name)
        self._reconnect_task = None  # this task; do not cancel it below
        await self.async_stop_streaming()

    async def _async_send_frame(self, frame: Bitmap, *, wait: bool) -> bool:
        """Send one frame; False if the streaming connection is down (reconnecting)."""
        async with self._lock:
            client = self._stream_client
            if client is None or not client.is_connected:
                return False
            try:
                await client.stream_frame(frame, wait=wait)
            except BadgeError as err:
                if not client.is_connected:
                    return False
                self.last_error = str(err)
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="command_failed",
                    translation_placeholders={"name": self.name, "error": str(err)},
                ) from err
        self._last_frame = frame
        self.stats["frames"] += 1
        self._async_set_preview(frame)
        return True

    async def _async_ensure_streaming(self) -> None:
        await self._async_cancel_task("_stream_task")
        if not self.streaming or self._stream_client is None:
            await self.async_start_streaming()

    async def async_stream_frame(self, bitmap: Bitmap, align: str = "center") -> None:
        """Show one 44x11 frame now, entering streaming mode if needed."""
        self._require_nextgen()
        frame = fit_columns(bitmap, DISPLAY_COLUMNS, align)
        await self._async_ensure_streaming()
        if not await self._async_send_frame(frame, wait=True):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"name": self.name, "error": "streaming connection lost"},
            )
        self._async_set_preview(frame, force=True)

    async def async_stream_text(
        self,
        text: str,
        *,
        font: str | None = None,
        effect: str = EFFECT_AUTO,
        fps: float = 10,
        duration: float | None = None,
        stop_after: bool = False,
    ) -> None:
        """Start showing text by streaming frames; returns once streaming has started.

        ``scroll`` (or ``auto`` with text wider than the display) moves the text
        across once, or repeatedly until ``duration`` seconds have passed.
        ``fixed`` shows the text centered. With ``stop_after`` the badge leaves
        streaming when done; otherwise the last frame stays up.
        """
        self._require_nextgen()
        bitmap = await self.async_render_text(text, font)
        scroll = effect == EFFECT_SCROLL or (effect == EFFECT_AUTO and bitmap.width > DISPLAY_COLUMNS)
        frames = scroll_frames(bitmap) if scroll else [fit_columns(bitmap, DISPLAY_COLUMNS, "center")]
        await self._async_ensure_streaming()
        self._stream_task = self.entry.async_create_background_task(
            self.hass,
            self._async_run_frames(frames, 1.0 / fps, duration, loop=scroll, stop_after=stop_after),
            f"{DOMAIN} stream text {self.address}",
        )

    async def _async_run_frames(
        self, frames: list[Bitmap], interval: float, duration: float | None, *, loop: bool, stop_after: bool
    ) -> None:
        deadline = None if duration is None else _monotonic() + duration
        try:
            while True:
                for frame in frames:
                    tick = _monotonic()
                    while not await self._async_send_frame(frame, wait=False):
                        if not self.streaming:
                            return
                        await _sleep(0.5)  # reconnecting
                    if deadline is not None and _monotonic() >= deadline:
                        break
                    await _sleep(max(0.0, interval - (_monotonic() - tick)))
                else:
                    if loop and deadline is not None:
                        continue
                    if deadline is not None and not loop:
                        # A fixed frame shown for ``duration`` seconds.
                        await _sleep(max(0.0, deadline - _monotonic()))
                break
        except HomeAssistantError as err:
            _LOGGER.warning("%s: streaming text failed: %s", self.name, err)
            return
        if self._last_frame is not None:
            self._async_set_preview(self._last_frame, force=True)
        if stop_after:
            self._stream_task = None  # this task; do not cancel it
            await self.async_stop_streaming()

    # preview -----------------------------------------------------------------

    @callback
    def _async_set_preview(self, bitmap: Bitmap, *, force: bool = False) -> None:
        self.preview = bitmap
        now = _monotonic()
        if not force and now - self._preview_notified < PREVIEW_STREAM_INTERVAL:
            return
        self._preview_notified = now
        self.preview_updated = dt_util.utcnow()
        for update_callback in list(self._preview_listeners):
            update_callback()

    # state -------------------------------------------------------------------

    @callback
    def _async_set_state(self, state: BadgeState) -> None:
        self.state = state
        self._async_save()
        self._async_notify()

    @callback
    def _async_set_settings(self, settings: BadgeSettings) -> None:
        self.settings = settings
        self._async_save()
        self._async_notify()

    @callback
    def _async_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 1)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "state": asdict(self.state),
            "settings": asdict(self.settings),
            "battery": self.battery,
            "battery_updated": self.battery_updated.isoformat() if self.battery_updated else None,
        }

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    def diagnostics(self) -> dict[str, Any]:
        service_info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return {
            "state": asdict(self.state),
            "settings": asdict(self.settings),
            "has_nextgen": self.has_nextgen,
            "reachable": self.reachable,
            "streaming": self.streaming,
            "battery": self.battery,
            "battery_updated": self.battery_updated.isoformat() if self.battery_updated else None,
            "last_upload": self.last_upload,
            "last_error": self.last_error,
            "stats": dict(self.stats),
            "advertisement": None
            if service_info is None
            else {
                "name": service_info.name,
                "rssi": service_info.rssi,
                "source": service_info.source,
                "service_uuids": service_info.service_uuids,
                "time": service_info.time,
            },
        }
