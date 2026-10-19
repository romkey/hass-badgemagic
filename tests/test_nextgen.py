"""Phase 2: next-gen configuration, OEM brightness, keep-Bluetooth linger, repair issue, wide text."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from badgemagic_ble import BadgeConnectionError, CommandError, Mode, OemBrightness, Xbm, render_text
from badgemagic_ble.client import KEEP_ADVERTISING_LINGER
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.text import DOMAIN as TEXT_DOMAIN
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, SERVICE_TURN_ON, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.badgemagic.const import CONF_KEEP_BLUETOOTH, CONF_WIDE_TEXT, DOMAIN, SERVICE_UUID

from .conftest import ADDRESS, inject_advertisement, make_service_info

BRIGHTNESS = "number.led_badge_magic_brightness"
ALWAYS_ON = "switch.led_badge_magic_always_on_bluetooth"
NAME_TEXT = "text.led_badge_magic_bluetooth_name"
MESSAGE = "text.led_badge_magic_message"
POWER_OFF = "button.led_badge_magic_power_off"
REBOOT = "button.led_badge_magic_restart"


async def set_text(hass: HomeAssistant, entity_id: str, value: str) -> None:
    await hass.services.async_call(TEXT_DOMAIN, "set_value", {ATTR_ENTITY_ID: entity_id, "value": value}, blocking=True)


async def set_brightness(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: BRIGHTNESS, ATTR_VALUE: value}, blocking=True
    )


# brightness -------------------------------------------------------------------


async def test_brightness_nextgen_applies_and_saves(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    assert hass.states.get(BRIGHTNESS).state == STATE_UNKNOWN
    await set_brightness(hass, 100)
    mock_client.set_brightness.assert_awaited_once_with(3)
    mock_client.save_config.assert_awaited_once()
    mock_client.upload.assert_not_called()
    assert hass.states.get(BRIGHTNESS).state == "100"

    mock_client.set_brightness.reset_mock()
    await set_brightness(hass, 60)  # snaps to the nearest step
    mock_client.set_brightness.assert_awaited_once_with(1)
    assert hass.states.get(BRIGHTNESS).state == "50"


async def test_oem_badge_has_no_nextgen_entities(
    hass: HomeAssistant, setup_oem_entry: MockConfigEntry, entity_registry: er.EntityRegistry
) -> None:
    entity_ids = {e.entity_id for e in er.async_entries_for_config_entry(entity_registry, setup_oem_entry.entry_id)}
    assert "number.lsled_brightness" in entity_ids
    assert "image.lsled_display_preview" in entity_ids
    assert not any(e.startswith(("button.", "switch.lsled_streaming", "switch.lsled_always")) for e in entity_ids)
    assert "text.lsled_bluetooth_name" not in entity_ids


async def test_brightness_oem_goes_in_upload_header(
    hass: HomeAssistant, setup_oem_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await set_text(hass, "text.lsled_message", "HI")
    assert mock_client.upload.await_args.kwargs["brightness"] is None
    assert mock_client.upload.await_args.kwargs["linger"] == 0.0
    mock_client.upload.reset_mock()

    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: "number.lsled_brightness", ATTR_VALUE: 50}, blocking=True
    )
    # The current message is re-uploaded with the new header byte.
    mock_client.upload.assert_awaited_once()
    assert mock_client.upload.await_args.kwargs["brightness"] is OemBrightness.HALF
    mock_client.set_brightness.assert_not_called()
    assert hass.states.get("number.lsled_brightness").state == "50"


async def test_brightness_oem_failure_keeps_old_value(
    hass: HomeAssistant, setup_oem_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await set_text(hass, "text.lsled_message", "HI")
    mock_client.connect.side_effect = BadgeConnectionError("gone")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: "number.lsled_brightness", ATTR_VALUE: 75}, blocking=True
        )
    assert hass.states.get("number.lsled_brightness").state == STATE_UNKNOWN
    assert setup_oem_entry.runtime_data.settings.brightness is None


async def test_nextgen_action_on_oem_badge_fails(hass: HomeAssistant, setup_oem_entry: MockConfigEntry) -> None:
    with pytest.raises(HomeAssistantError) as exc_info:
        await setup_oem_entry.runtime_data.async_set_always_on(True)
    assert exc_info.value.translation_key == "nextgen_required"


# switches, name, buttons ------------------------------------------------------


async def test_always_on_switch(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    assert hass.states.get(ALWAYS_ON).state == STATE_UNKNOWN
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ALWAYS_ON}, blocking=True)
    mock_client.set_always_on.assert_awaited_once_with(True)
    mock_client.save_config.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()
    assert hass.states.get(ALWAYS_ON).state == STATE_ON


async def test_reset_after_upload(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    coordinator = setup_entry.runtime_data
    await coordinator.async_set_reset_after_upload(True)
    mock_client.set_reset_after_upload.assert_awaited_once_with(True)
    mock_client.save_config.assert_awaited_once()
    assert coordinator.settings.reset_after_upload is True


async def test_command_error_is_reported(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.set_always_on.side_effect = CommandError(0x04, 0xFF)
    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: ALWAYS_ON}, blocking=True)
    assert exc_info.value.translation_key == "command_failed"
    mock_client.save_config.assert_not_called()
    assert hass.states.get(ALWAYS_ON).state == STATE_UNKNOWN


async def test_rename(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock, device_registry: dr.DeviceRegistry
) -> None:
    assert hass.states.get(NAME_TEXT).state == "LED Badge Magic"
    await set_text(hass, NAME_TEXT, " Kitchen badge ")
    mock_client.set_name.assert_awaited_once_with("Kitchen badge")
    mock_client.save_config.assert_awaited_once()
    assert hass.states.get(NAME_TEXT).state == "Kitchen badge"
    device = device_registry.async_get_device_by_identifier((DOMAIN, ADDRESS), setup_entry.entry_id)
    assert device is not None and device.name == "Kitchen badge"
    assert setup_entry.title == "Kitchen badge"


async def test_rename_rejects_long_names(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    with pytest.raises(ServiceValidationError):
        await set_text(hass, NAME_TEXT, "ü" * 11)  # 11 characters, 22 bytes
    mock_client.set_name.assert_not_called()


@pytest.mark.parametrize(("entity_id", "method"), [(POWER_OFF, "power_off"), (REBOOT, "reboot")])
async def test_power_buttons(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock, entity_id: str, method: str
) -> None:
    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True)
    getattr(mock_client, method).assert_awaited_once()
    mock_client.save_config.assert_not_called()


async def test_load_defaults_resets_remembered_settings(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    coordinator = setup_entry.runtime_data
    await set_brightness(hass, 100)
    mock_client.save_config.reset_mock()
    await coordinator.async_load_defaults()
    mock_client.load_defaults.assert_awaited_once()
    mock_client.save_config.assert_awaited_once()
    await hass.async_block_till_done()
    assert hass.states.get(BRIGHTNESS).state == "25"
    assert hass.states.get(ALWAYS_ON).state == "off"
    assert hass.states.get(NAME_TEXT).state == "LED Badge Magic"


async def test_save_config(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    await setup_entry.runtime_data.async_save_config()
    mock_client.save_config.assert_awaited_once()


async def test_settings_restored(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock, hass_storage: dict
) -> None:
    hass_storage[f"{DOMAIN}.{config_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{config_entry.entry_id}",
        "data": {"settings": {"brightness": 75, "always_on": True, "device_name": "Desk"}},
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BRIGHTNESS).state == "75"
    assert hass.states.get(ALWAYS_ON).state == STATE_ON
    assert hass.states.get(NAME_TEXT).state == "Desk"


# splash -----------------------------------------------------------------------


@pytest.fixture
def device_id(setup_entry: MockConfigEntry, device_registry: dr.DeviceRegistry) -> str:
    device = device_registry.async_get_device_by_identifier((DOMAIN, ADDRESS), setup_entry.entry_id)
    assert device is not None
    return device.id


async def test_set_splash_screen(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, tmp_path: Path
) -> None:
    image = Image.new("L", (16, 22), 0)
    image.putpixel((0, 0), 255)
    path = tmp_path / "splash.png"
    image.save(path)
    hass.config.allowlist_external_dirs = {str(tmp_path)}

    await hass.services.async_call(
        DOMAIN,
        "set_splash_screen",
        {ATTR_DEVICE_ID: device_id, "image": str(path), "frame_height": 11, "speed_ms": 40},
        blocking=True,
    )
    xbm, frame_height = mock_client.set_splash.await_args.args
    assert isinstance(xbm, Xbm)
    assert (xbm.width, xbm.height) == (16, 22)
    assert xbm[0, 0] and not xbm[1, 0]
    assert frame_height == 11
    mock_client.set_splash_speed.assert_awaited_once_with(40)
    mock_client.save_config.assert_awaited_once()


async def test_set_splash_screen_too_large(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, tmp_path: Path
) -> None:
    path = tmp_path / "big.png"
    Image.new("L", (48, 44), 255).save(path)  # 6 bytes x 44 rows = 264 bytes
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN, "set_splash_screen", {ATTR_DEVICE_ID: device_id, "image": str(path)}, blocking=True
        )
    assert exc_info.value.translation_key == "splash_too_large"
    mock_client.set_splash.assert_not_called()


# keep Bluetooth on, repair issue -----------------------------------------------


async def test_upload_lingers_on_open_firmware(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await set_text(hass, MESSAGE, "HI")
    assert mock_client.upload.await_args.kwargs["linger"] == KEEP_ADVERTISING_LINGER

    mock_client.upload.reset_mock()
    hass.config_entries.async_update_entry(setup_entry, options={**setup_entry.options, CONF_KEEP_BLUETOOTH: False})
    await set_text(hass, MESSAGE, "HO")
    assert mock_client.upload.await_args.kwargs["linger"] == 0.0


async def test_repair_issue_when_bluetooth_goes_off_after_upload(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock, issue_registry: ir.IssueRegistry
) -> None:
    coordinator = setup_entry.runtime_data
    await set_text(hass, MESSAGE, "HI")
    coordinator._async_on_unavailable(None)
    issue = issue_registry.async_get_issue(DOMAIN, coordinator.issue_id)
    assert issue is not None
    assert issue.translation_key == "bluetooth_off_after_upload"
    assert issue.translation_placeholders == {"name": "LED Badge Magic"}

    inject_advertisement(hass, make_service_info(service_data={SERVICE_UUID: b"\x02"}))
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, coordinator.issue_id) is None


async def test_no_repair_issue_without_recent_upload(
    hass: HomeAssistant, setup_entry: MockConfigEntry, issue_registry: ir.IssueRegistry
) -> None:
    coordinator = setup_entry.runtime_data
    coordinator._async_on_unavailable(None)
    assert issue_registry.async_get_issue(DOMAIN, coordinator.issue_id) is None


# wide text --------------------------------------------------------------------

WIDE = "Hello wide world"  # far wider than 44 columns


@pytest.mark.parametrize(
    ("option", "mode", "width"),
    [
        ("page", Mode.FIXED, render_text(WIDE).width),
        ("scroll", Mode.SCROLL_LEFT, render_text(WIDE).width),
        ("clip", Mode.FIXED, 44),
    ],
)
async def test_wide_text_in_fixed_mode(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    mock_client: MagicMock,
    option: str,
    mode: Mode,
    width: int,
) -> None:
    hass.config_entries.async_update_entry(setup_entry, options={**setup_entry.options, CONF_WIDE_TEXT: option})
    await setup_entry.runtime_data.async_update_settings(mode="fixed")
    await set_text(hass, MESSAGE, WIDE)
    (message,) = mock_client.upload.await_args.args[0]
    assert message.mode is mode
    assert message.bitmap.width == width
    # The Mode entity keeps what the user chose.
    assert hass.states.get("select.led_badge_magic_mode").state == "fixed"


async def test_wide_text_option_ignores_animation_mode(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    hass.config_entries.async_update_entry(setup_entry, options={**setup_entry.options, CONF_WIDE_TEXT: "clip"})
    await setup_entry.runtime_data.async_update_settings(mode="animation")
    await set_text(hass, MESSAGE, WIDE)
    (message,) = mock_client.upload.await_args.args[0]
    assert message.mode is Mode.ANIMATION
    assert message.bitmap.width > 44


# preview ----------------------------------------------------------------------


async def test_preview_image(
    hass: HomeAssistant, setup_entry: MockConfigEntry, hass_client: ClientSessionGenerator
) -> None:
    image_entity = "image.led_badge_magic_display_preview"
    before = hass.states.get(image_entity).state
    await set_text(hass, MESSAGE, "HI")
    await hass.async_block_till_done()
    assert hass.states.get(image_entity).state != before
    assert setup_entry.runtime_data.preview == render_text("HI")

    client = await hass_client()
    response = await client.get(f"/api/image_proxy/{image_entity}")
    assert response.status == 200
    assert response.content_type == "image/png"
    body = await response.read()
    assert body.startswith(b"\x89PNG")
