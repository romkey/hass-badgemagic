"""badgemagic.send_messages action tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.badgemagic.badgemagic_ble import Mode, PinRejected, PinRequired, load_image, render_text
from custom_components.badgemagic.const import DOMAIN, SERVICE_SEND_MESSAGES

from .conftest import ADDRESS

TEXT_ENTITY = "text.led_badge_magic_message"


@pytest.fixture
def device_id(setup_entry: MockConfigEntry, device_registry: dr.DeviceRegistry) -> str:
    device = device_registry.async_get_device_by_identifier((DOMAIN, ADDRESS), setup_entry.entry_id)
    assert device is not None
    return device.id


@pytest.fixture
def image_path(tmp_path: Path) -> Path:
    """An 8x11 picture: a lit border."""
    image = Image.new("L", (8, 11), 0)
    for x in range(8):
        image.putpixel((x, 0), 255)
        image.putpixel((x, 10), 255)
    for y in range(11):
        image.putpixel((0, y), 255)
        image.putpixel((7, y), 255)
    path = tmp_path / "border.png"
    image.save(path)
    return path


async def send(hass: HomeAssistant, data: dict) -> None:
    await hass.services.async_call(DOMAIN, SERVICE_SEND_MESSAGES, data, blocking=True)


async def test_send_text_and_image(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, image_path: Path
) -> None:
    hass.config.allowlist_external_dirs = {str(image_path.parent)}
    await send(
        hass,
        {
            ATTR_DEVICE_ID: device_id,
            "messages": [
                {"text": "Doorbell", "mode": "fixed", "speed": 8, "flash": True, "font": "compact"},
                {"image": str(image_path), "mode": "picture", "marquee": True},
                {"text": "Defaults"},
            ],
            "pin": "6512",
            "set_clock": False,
        },
    )

    mock_client.upload.assert_awaited_once()
    messages = mock_client.upload.await_args.args[0]
    assert len(messages) == 3
    first, second, third = messages
    assert first.bitmap == render_text("Doorbell", "compact")
    assert (first.mode, first.speed, first.flash, first.marquee) == (Mode.FIXED, 8, True, False)
    assert second.bitmap == load_image(image_path)
    assert (second.mode, second.marquee) == (Mode.PICTURE, True)
    # Fields left out come from the badge's options.
    assert third.bitmap == render_text("Defaults", "normal")
    assert (third.mode, third.speed, third.flash, third.marquee) == (Mode.SCROLL_LEFT, 4, False, False)
    assert mock_client.upload.await_args.kwargs["pin"] == "6512"
    assert mock_client.upload.await_args.kwargs["set_clock"] is False
    # The action does not change the single-message entities.
    assert hass.states.get(TEXT_ENTITY).state == "unknown"


async def test_target_by_entity(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    await send(hass, {ATTR_ENTITY_ID: TEXT_ENTITY, "messages": [{"text": "Hi"}]})
    mock_client.upload.assert_awaited_once()
    assert mock_client.upload.await_args.kwargs["set_clock"] is True


async def test_set_clock_defaults_to_option(
    hass: HomeAssistant, setup_entry: MockConfigEntry, device_id: str, mock_client: MagicMock
) -> None:
    hass.config_entries.async_update_entry(setup_entry, options={**setup_entry.options, "set_clock": False})
    await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"text": "Hi"}]})
    assert mock_client.upload.await_args.kwargs["set_clock"] is False


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"text": str(i)} for i in range(9)],
        [{"text": "a", "image": "/tmp/x.png"}],
        [{"mode": "fixed"}],
        [{"text": "a", "mode": "sideways"}],
        [{"text": "a", "speed": 9}],
        [{"text": "a", "font": "huge"}],
    ],
)
async def test_invalid_messages(hass: HomeAssistant, device_id: str, mock_client: MagicMock, messages: list) -> None:
    with pytest.raises(Exception) as exc_info:  # noqa: PT011 - voluptuous or probatio Invalid
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": messages})
    assert "Invalid" in type(exc_info.value).__name__
    mock_client.upload.assert_not_called()


async def test_invalid_pin(hass: HomeAssistant, device_id: str, mock_client: MagicMock) -> None:
    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"text": "a"}], "pin": "12345"})
    assert "Invalid" in type(exc_info.value).__name__


async def test_image_path_not_allowed(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, image_path: Path
) -> None:
    hass.config.allowlist_external_dirs = set()
    with pytest.raises(ServiceValidationError) as exc_info:
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"image": str(image_path)}]})
    assert exc_info.value.translation_key == "path_not_allowed"
    mock_client.upload.assert_not_called()


async def test_image_missing(hass: HomeAssistant, device_id: str, mock_client: MagicMock, tmp_path: Path) -> None:
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    with pytest.raises(ServiceValidationError) as exc_info:
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"image": str(tmp_path / "nope.png")}]})
    assert exc_info.value.translation_key == "image_unreadable"


async def test_media_source_needs_media_source(hass: HomeAssistant, device_id: str, mock_client: MagicMock) -> None:
    with pytest.raises(ServiceValidationError) as exc_info:
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"image": "media-source://media_source/local/x.png"}]})
    assert exc_info.value.translation_key == "media_not_local"


@pytest.mark.parametrize(
    ("error", "key"),
    [(PinRequired("needs PIN"), "pin_required"), (PinRejected("wrong PIN"), "pin_rejected")],
)
async def test_pin_errors(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, error: Exception, key: str
) -> None:
    mock_client.upload.side_effect = error
    with pytest.raises(HomeAssistantError) as exc_info:
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"text": "a"}]})
    assert exc_info.value.translation_key == key


async def test_no_target(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    with pytest.raises(ServiceValidationError) as exc_info:
        await send(hass, {ATTR_DEVICE_ID: "not-a-device", "messages": [{"text": "a"}]})
    assert exc_info.value.translation_key == "no_target"


async def test_unloaded_badge(
    hass: HomeAssistant, setup_entry: MockConfigEntry, device_id: str, mock_client: MagicMock
) -> None:
    await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as exc_info:
        await send(hass, {ATTR_DEVICE_ID: device_id, "messages": [{"text": "a"}]})
    assert exc_info.value.translation_key == "entry_not_loaded"
