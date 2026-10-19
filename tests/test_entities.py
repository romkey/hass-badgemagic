"""Entity, coordinator and diagnostics tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.notify import ATTR_MESSAGE, SERVICE_SEND_MESSAGE
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.text import DOMAIN as TEXT_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_FINAL_WRITE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.badgemagic.badgemagic_ble import BadgeConnectionError, Mode, UploadError, render_text
from custom_components.badgemagic.const import DOMAIN, SERVICE_UUID
from custom_components.badgemagic.diagnostics import async_get_config_entry_diagnostics

from .conftest import ADDRESS, FIRMWARE, inject_advertisement, make_service_info

TEXT = "text.led_badge_magic_message"
NOTIFY = "notify.led_badge_magic_message"
MODE = "select.led_badge_magic_mode"
SPEED = "number.led_badge_magic_speed"
FLASH = "switch.led_badge_magic_flash"
MARQUEE = "switch.led_badge_magic_marquee"
BATTERY = "sensor.led_badge_magic_battery"
FIRMWARE_SENSOR = "sensor.led_badge_magic_firmware_version"
REACHABLE = "binary_sensor.led_badge_magic_bluetooth_reachable"
NEXTGEN_ENTITIES = {
    "number.led_badge_magic_brightness",
    "switch.led_badge_magic_always_on_bluetooth",
    "switch.led_badge_magic_restart_after_upload",
    "switch.led_badge_magic_streaming",
    "text.led_badge_magic_bluetooth_name",
    "button.led_badge_magic_power_off",
    "button.led_badge_magic_restart",
    "button.led_badge_magic_save_settings",
    "button.led_badge_magic_restore_default_settings",
    "binary_sensor.led_badge_magic_streaming_active",
    "image.led_badge_magic_display_preview",
}


def uploaded_messages(mock_client: MagicMock) -> list:
    mock_client.upload.assert_awaited_once()
    return list(mock_client.upload.await_args.args[0])


async def set_text(hass: HomeAssistant, value: str) -> None:
    await hass.services.async_call(
        TEXT_DOMAIN, "set_value", {ATTR_ENTITY_ID: TEXT, "value": value}, blocking=True
    )


async def test_setup_creates_device_and_entities(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    device = device_registry.async_get_device_by_identifier((DOMAIN, ADDRESS), setup_entry.entry_id)
    assert device is not None
    assert device.manufacturer == "FOSSASIA"
    assert device.model == "BM1144-C"
    assert device.sw_version == FIRMWARE
    assert device.hw_version == "REV3"
    assert (dr.CONNECTION_BLUETOOTH, ADDRESS) in device.connections

    entities = er.async_entries_for_config_entry(entity_registry, setup_entry.entry_id)
    assert {entity.entity_id for entity in entities} == {
        TEXT, NOTIFY, MODE, SPEED, FLASH, MARQUEE, BATTERY, FIRMWARE_SENSOR, REACHABLE
    } | NEXTGEN_ENTITIES
    assert all(entity.device_id == device.id for entity in entities)

    assert hass.states.get(TEXT).state == STATE_UNKNOWN
    assert hass.states.get(MODE).state == "scroll_left"
    assert hass.states.get(SPEED).state == "4"
    assert hass.states.get(FLASH).state == STATE_OFF
    assert hass.states.get(MARQUEE).state == STATE_OFF
    assert hass.states.get(FIRMWARE_SENSOR).state == FIRMWARE
    assert hass.states.get(REACHABLE).state == STATE_ON
    # The badge was advertising at startup, so the battery was read.
    assert hass.states.get(BATTERY).state == "84"


async def test_set_text_uploads(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    await set_text(hass, "HELLO")

    (message,) = uploaded_messages(mock_client)
    assert message.bitmap == render_text("HELLO", "normal")
    assert message.mode is Mode.SCROLL_LEFT
    assert message.speed == 4
    assert not message.flash
    assert not message.marquee
    assert mock_client.upload.await_args.kwargs["set_clock"] is True
    assert mock_client.upload.await_args.kwargs["pin"] is None
    # The battery is read before the upload, and the connection is closed after.
    mock_client.read_battery.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()
    assert hass.states.get(TEXT).state == "HELLO"


async def test_notify_uploads_and_updates_text(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await hass.services.async_call(
        NOTIFY_DOMAIN, SERVICE_SEND_MESSAGE, {ATTR_ENTITY_ID: NOTIFY, ATTR_MESSAGE: "Doorbell"}, blocking=True
    )
    (message,) = uploaded_messages(mock_client)
    assert message.bitmap == render_text("Doorbell", "normal")
    assert hass.states.get(TEXT).state == "Doorbell"


async def test_font_option_is_used(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    hass.config_entries.async_update_entry(setup_entry, options={**setup_entry.options, "font": "tiny"})
    await set_text(hass, "HELLO")
    (message,) = uploaded_messages(mock_client)
    assert message.bitmap == render_text("HELLO", "tiny")


@pytest.mark.parametrize(
    ("domain", "service", "data", "entity_id", "attribute", "expected", "state"),
    [
        (SELECT_DOMAIN, SERVICE_SELECT_OPTION, {ATTR_OPTION: "laser"}, MODE, "mode", Mode.LASER, "laser"),
        (NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_VALUE: 8}, SPEED, "speed", 8, "8"),
        (SWITCH_DOMAIN, SERVICE_TURN_ON, {}, FLASH, "flash", True, STATE_ON),
        (SWITCH_DOMAIN, SERVICE_TURN_ON, {}, MARQUEE, "marquee", True, STATE_ON),
    ],
)
async def test_setting_change_reuploads_message(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    mock_client: MagicMock,
    domain: str,
    service: str,
    data: dict,
    entity_id: str,
    attribute: str,
    expected: object,
    state: str,
) -> None:
    await set_text(hass, "HELLO")
    mock_client.upload.reset_mock()

    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)
    (message,) = uploaded_messages(mock_client)
    assert getattr(message, attribute) == expected
    assert message.bitmap == render_text("HELLO", "normal")
    assert hass.states.get(entity_id).state == state


async def test_setting_change_without_message_does_not_upload(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: MODE, ATTR_OPTION: "fixed"}, blocking=True
    )
    mock_client.upload.assert_not_called()
    assert hass.states.get(MODE).state == "fixed"

    await set_text(hass, "HI")
    (message,) = uploaded_messages(mock_client)
    assert message.mode is Mode.FIXED


async def test_switch_off(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: FLASH}, blocking=True)
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FLASH}, blocking=True)
    assert hass.states.get(FLASH).state == STATE_OFF


async def test_not_advertising_raises(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    """With no advertisement, actions fail with a hint about BT-PAIRING mode."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(REACHABLE).state == STATE_OFF
    assert hass.states.get(BATTERY).state == STATE_UNKNOWN

    with pytest.raises(HomeAssistantError) as exc_info:
        await set_text(hass, "HELLO")
    assert exc_info.value.translation_key == "not_advertising"
    assert "BT-PAIRING" in str(exc_info.value)
    mock_client.connect.assert_not_called()
    assert hass.states.get(TEXT).state == STATE_UNKNOWN


async def test_connect_failure_keeps_state(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.connect.side_effect = BadgeConnectionError("out of range")
    with pytest.raises(HomeAssistantError) as exc_info:
        await set_text(hass, "HELLO")
    assert exc_info.value.translation_key == "cannot_connect"
    assert hass.states.get(TEXT).state == STATE_UNKNOWN


async def test_upload_failure_keeps_previous_settings(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await set_text(hass, "HELLO")
    mock_client.upload.side_effect = UploadError("packet 2/9 rejected")
    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            SELECT_DOMAIN, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: MODE, ATTR_OPTION: "laser"}, blocking=True
        )
    assert exc_info.value.translation_key == "upload_failed"
    assert hass.states.get(MODE).state == "scroll_left"
    mock_client.disconnect.assert_awaited()


async def test_state_restored_after_restart(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock, hass_storage: dict
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {
            "state": {"text": "Saved", "mode": "fixed", "speed": 2, "flash": True, "marquee": False},
            "battery": 55,
            "battery_updated": "2026-10-18T12:00:00+00:00",
        },
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TEXT).state == "Saved"
    assert hass.states.get(MODE).state == "fixed"
    assert hass.states.get(SPEED).state == "2"
    assert hass.states.get(FLASH).state == STATE_ON
    assert hass.states.get(BATTERY).state == "55"


async def test_state_is_saved(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock, hass_storage: dict
) -> None:
    await set_text(hass, "Persist me")
    # Pending saves are flushed when Home Assistant shuts down.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done(wait_background_tasks=True)
    saved = hass_storage[f"{DOMAIN}.{setup_entry.entry_id}"]["data"]
    assert saved["state"]["text"] == "Persist me"
    assert saved["battery"] == 84


async def test_availability_follows_advertisements(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    coordinator = setup_entry.runtime_data
    coordinator._async_on_unavailable(None)
    await hass.async_block_till_done()
    assert hass.states.get(REACHABLE).state == STATE_OFF
    # The last battery reading is kept while the badge is away.
    assert hass.states.get(BATTERY).state == "84"

    # Once a badge goes unavailable, Home Assistant's Bluetooth manager drops
    # its history, so its next advertisement counts as new. Simulate that with
    # a changed payload, since identical repeats are not passed on.
    inject_advertisement(hass, make_service_info(service_data={SERVICE_UUID: b"\x01"}))
    await hass.async_block_till_done()
    assert hass.states.get(REACHABLE).state == STATE_ON


async def test_battery_refreshed_hourly(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.read_battery.return_value = 70
    freezer.tick(timedelta(hours=1, seconds=1))
    inject_advertisement(hass)  # still advertising an hour later
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.read_battery.assert_awaited()
    mock_client.disconnect.assert_awaited()
    assert hass.states.get(BATTERY).state == "70"


async def test_battery_not_reread_within_the_hour(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    inject_advertisement(hass, make_service_info(service_data={SERVICE_UUID: b"\x01"}))
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.connect.assert_not_called()


async def test_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    await set_text(hass, "HI")
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)
    assert diagnostics["entry"]["data"]["firmware_revision"] == FIRMWARE
    badge = diagnostics["badge"]
    assert badge["state"]["text"] == "HI"
    assert badge["reachable"] is True
    assert badge["battery"] == 84
    assert badge["stats"]["uploads"] == 1
    assert badge["last_upload"]["messages"] == 1
    frame = bytes.fromhex(badge["last_upload"]["frame"])
    assert frame.startswith(b"wang\x00")
    assert len(frame) == badge["last_upload"]["frame_bytes"]
    assert badge["advertisement"]["name"] == "LED Badge Magic"
