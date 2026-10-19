"""Phase 3: streaming switch, stream_frame and stream_text actions, reconnects."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from badgemagic_ble import BadgeConnectionError, Bitmap, render_text
from badgemagic_ble.render import fit_columns
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.text import DOMAIN as TEXT_DOMAIN
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from PIL import Image
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.badgemagic.const import DOMAIN
from custom_components.badgemagic.coordinator import scroll_frames

from .conftest import ADDRESS

STREAMING = "switch.led_badge_magic_streaming"
STREAMING_ACTIVE = "binary_sensor.led_badge_magic_streaming_active"
MESSAGE = "text.led_badge_magic_message"


@pytest.fixture
def device_id(setup_entry: MockConfigEntry, device_registry: dr.DeviceRegistry) -> str:
    device = device_registry.async_get_device_by_identifier((DOMAIN, ADDRESS), setup_entry.entry_id)
    assert device is not None
    return device.id


def sent_frames(mock_client: MagicMock) -> list[Bitmap]:
    return [call.args[0] for call in mock_client.stream_frame.await_args_list]


async def turn(hass: HomeAssistant, on: bool) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON if on else SERVICE_TURN_OFF, {ATTR_ENTITY_ID: STREAMING}, blocking=True
    )


async def test_streaming_switch(hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock) -> None:
    assert hass.states.get(STREAMING).state == STATE_OFF
    await turn(hass, True)
    mock_client.start_streaming.assert_awaited_once()
    # The connection stays open while streaming.
    mock_client.disconnect.assert_not_called()
    assert hass.states.get(STREAMING).state == STATE_ON
    assert hass.states.get(STREAMING_ACTIVE).state == STATE_ON
    callback = mock_client.client_class.call_args.kwargs["disconnected_callback"]
    assert callback is not None

    await turn(hass, False)
    mock_client.stop_streaming.assert_awaited_once()
    mock_client.disconnect.assert_awaited_once()
    assert hass.states.get(STREAMING).state == STATE_OFF
    assert hass.states.get(STREAMING_ACTIVE).state == STATE_OFF


async def test_commands_use_the_streaming_connection(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await turn(hass, True)
    mock_client.connect.reset_mock()
    await setup_entry.runtime_data.async_set_brightness(75)
    mock_client.set_brightness.assert_awaited_once_with(2)
    mock_client.connect.assert_not_called()
    mock_client.disconnect.assert_not_called()


async def test_stream_frame_text_starts_streaming(
    hass: HomeAssistant, device_id: str, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await hass.services.async_call(
        DOMAIN, "stream_frame", {ATTR_DEVICE_ID: device_id, "text": "21°", "font": "compact"}, blocking=True
    )
    mock_client.start_streaming.assert_awaited_once()
    (frame,) = sent_frames(mock_client)
    assert frame == fit_columns(render_text("21°", "compact"), 44, "center")
    assert mock_client.stream_frame.await_args.kwargs["wait"] is True
    assert hass.states.get(STREAMING).state == STATE_ON
    assert setup_entry.runtime_data.preview == frame

    # A second frame reuses the open stream.
    await hass.services.async_call(
        DOMAIN, "stream_frame", {ATTR_DEVICE_ID: device_id, "text": "22°", "align": "left"}, blocking=True
    )
    mock_client.start_streaming.assert_awaited_once()
    assert sent_frames(mock_client)[-1] == fit_columns(render_text("22°"), 44, "left")


async def test_stream_frame_image(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, tmp_path: Path
) -> None:
    path = tmp_path / "dot.png"
    image = Image.new("L", (4, 11), 0)
    image.putpixel((1, 5), 255)
    image.save(path)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    await hass.services.async_call(
        DOMAIN, "stream_frame", {ATTR_DEVICE_ID: device_id, "image": str(path)}, blocking=True
    )
    (frame,) = sent_frames(mock_client)
    assert frame.width == 44
    assert frame[21, 5]  # centered: 20 blank columns, then the 4-wide image


async def test_stream_text_scrolls_then_stops(
    hass: HomeAssistant, device_id: str, setup_entry: MockConfigEntry, mock_client: MagicMock, no_sleep: AsyncMock
) -> None:
    await hass.services.async_call(
        DOMAIN,
        "stream_text",
        {ATTR_DEVICE_ID: device_id, "text": "HI", "effect": "scroll", "fps": 20, "stop_after": True},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    expected = scroll_frames(render_text("HI"))
    assert sent_frames(mock_client) == expected
    assert all(call.kwargs["wait"] is False for call in mock_client.stream_frame.await_args_list)
    # One column per frame at 20 fps.
    assert all(call.args[0] <= 0.05 for call in no_sleep.await_args_list)
    mock_client.stop_streaming.assert_awaited_once()
    assert hass.states.get(STREAMING).state == STATE_OFF
    assert setup_entry.runtime_data.stats["frames"] == len(expected)


async def test_stream_text_fixed_keeps_frame(
    hass: HomeAssistant, device_id: str, mock_client: MagicMock, no_sleep: AsyncMock
) -> None:
    await hass.services.async_call(
        DOMAIN, "stream_text", {ATTR_DEVICE_ID: device_id, "text": "OK"}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sent_frames(mock_client) == [fit_columns(render_text("OK"), 44, "center")]
    mock_client.stop_streaming.assert_not_called()
    assert hass.states.get(STREAMING).state == STATE_ON


async def test_stream_text_loops_for_duration(
    hass: HomeAssistant, device_id: str, setup_entry: MockConfigEntry, mock_client: MagicMock, no_sleep: AsyncMock
) -> None:
    coordinator = setup_entry.runtime_data
    frames_per_pass = len(scroll_frames(render_text("A")))
    clock = iter(range(100_000))
    # Each clock read advances a fake clock by 1 ms.
    with patch("custom_components.badgemagic.coordinator._monotonic", side_effect=lambda: next(clock) / 1000):
        await coordinator.async_stream_text("A", effect="scroll", fps=20, duration=0.5)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(sent_frames(mock_client)) > frames_per_pass  # looped past one pass


async def test_new_stream_replaces_running_text(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    coordinator = setup_entry.runtime_data
    # Real sleeps: the first scroll is still running when the frame arrives.
    await coordinator.async_stream_text("A long scrolling text", effect="scroll", fps=1)
    await coordinator.async_stream_frame(render_text("X"))
    await hass.async_block_till_done()
    assert coordinator._stream_task is None
    assert sent_frames(mock_client)[-1] == fit_columns(render_text("X"), 44, "center")


async def test_upload_leaves_streaming_first(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await turn(hass, True)
    await hass.services.async_call(TEXT_DOMAIN, "set_value", {ATTR_ENTITY_ID: MESSAGE, "value": "HI"}, blocking=True)
    mock_client.stop_streaming.assert_awaited_once()
    mock_client.upload.assert_awaited_once()
    assert hass.states.get(STREAMING).state == STATE_OFF


async def test_stream_reconnects_after_drop(
    hass: HomeAssistant, device_id: str, setup_entry: MockConfigEntry, mock_client: MagicMock, no_sleep: AsyncMock
) -> None:
    coordinator = setup_entry.runtime_data
    await hass.services.async_call(DOMAIN, "stream_frame", {ATTR_DEVICE_ID: device_id, "text": "HI"}, blocking=True)
    callback = mock_client.client_class.call_args.kwargs["disconnected_callback"]
    mock_client.start_streaming.reset_mock()
    mock_client.stream_frame.reset_mock()

    callback(mock_client)
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.start_streaming.assert_awaited_once()
    # The last frame is shown again.
    assert sent_frames(mock_client) == [fit_columns(render_text("HI"), 44, "center")]
    assert coordinator.streaming
    assert coordinator.stats["stream_drops"] == 1


async def test_stream_gives_up_after_failed_reconnects(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock, no_sleep: AsyncMock
) -> None:
    coordinator = setup_entry.runtime_data
    await turn(hass, True)
    callback = mock_client.client_class.call_args.kwargs["disconnected_callback"]
    mock_client.connect.side_effect = BadgeConnectionError("gone")

    callback(mock_client)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_client.connect.await_count == 1 + 3  # the first connection, then three attempts
    assert not coordinator.streaming
    assert hass.states.get(STREAMING).state == STATE_OFF


async def test_streaming_requires_open_firmware(
    hass: HomeAssistant, setup_oem_entry: MockConfigEntry, device_registry: dr.DeviceRegistry
) -> None:
    device = device_registry.async_get_device_by_identifier((DOMAIN, ADDRESS), setup_oem_entry.entry_id)
    assert device is not None
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(DOMAIN, "stream_text", {ATTR_DEVICE_ID: device.id, "text": "HI"}, blocking=True)
    assert exc_info.value.translation_key == "nextgen_required"


async def test_unload_stops_streaming(
    hass: HomeAssistant, setup_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await turn(hass, True)
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.stop_streaming.assert_awaited_once()
    mock_client.disconnect.assert_awaited()
