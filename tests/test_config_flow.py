"""Config flow and options flow tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from badgemagic_ble import BadgeConnectionError
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.badgemagic.const import (
    CONF_FIRMWARE,
    CONF_FLASH,
    CONF_FONT,
    CONF_HAS_NEXTGEN,
    CONF_KEEP_BLUETOOTH,
    CONF_MARQUEE,
    CONF_MODE,
    CONF_MODEL,
    CONF_SET_CLOCK,
    CONF_SPEED,
    CONF_WIDE_TEXT,
    DEFAULT_OPTIONS,
    DOMAIN,
)

from .conftest import ADDRESS, FIRMWARE, NAME, inject_advertisement, make_service_info


async def test_bluetooth_discovery_starts_flow(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The manifest's service UUID matcher starts a discovery flow."""
    # Discovery flows are held back until Home Assistant has started.
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    inject_advertisement(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_BLUETOOTH
    assert flows[0]["step_id"] == "bluetooth_confirm"


async def test_unrelated_device_not_discovered(hass: HomeAssistant, mock_client: MagicMock) -> None:
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    inject_advertisement(hass, make_service_info(address="11:22:33:44:55:66", name="Other", service_uuids=[]))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_bluetooth_confirm_creates_entry(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": NAME}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["result"].unique_id == ADDRESS
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_MODEL] == "BM1144-C"
    assert result["data"][CONF_FIRMWARE] == FIRMWARE
    assert result["data"][CONF_HAS_NEXTGEN] is True
    assert result["options"] == DEFAULT_OPTIONS
    mock_client.read_device_info.assert_awaited_once()
    # The flow only reads; it must not upload (that would switch the badge's Bluetooth off).
    mock_client.upload.assert_not_called()


async def test_bluetooth_confirm_oem_badge(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """OEM firmware has no next-gen service and no device information."""
    mock_client.has_nextgen = False
    mock_client.read_device_info.return_value = type(mock_client.read_device_info.return_value)()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info(name="LSLED")
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "LSLED"
    assert result["data"][CONF_HAS_NEXTGEN] is False
    assert result["data"][CONF_MODEL] is None


async def test_bluetooth_cannot_connect_then_recovers(hass: HomeAssistant, mock_client: MagicMock) -> None:
    mock_client.__aenter__.side_effect = BadgeConnectionError("timed out")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.__aenter__.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_bluetooth_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_lists_badges(hass: HomeAssistant, mock_client: MagicMock) -> None:
    inject_advertisement(hass)
    inject_advertisement(hass, make_service_info(address="11:22:33:44:55:66", name="Phone", service_uuids=[]))
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    choices = result["data_schema"].schema[CONF_ADDRESS].container
    assert choices == {ADDRESS: f"{NAME} ({ADDRESS})"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == ADDRESS
    assert result["data"][CONF_MODEL] == "BM1144-C"


async def test_user_step_cannot_connect(hass: HomeAssistant, mock_client: MagicMock) -> None:
    inject_advertisement(hass)
    await hass.async_block_till_done()
    mock_client.read_device_info.side_effect = BadgeConnectionError("not connected")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_no_devices(hass: HomeAssistant, mock_client: MagicMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_skips_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    inject_advertisement(hass)
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_options_flow(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    new_options = {
        CONF_FONT: "tiny",
        CONF_MODE: "fixed",
        CONF_SPEED: 7.0,
        CONF_FLASH: True,
        CONF_MARQUEE: True,
        CONF_SET_CLOCK: False,
        CONF_WIDE_TEXT: "scroll",
        CONF_KEEP_BLUETOOTH: False,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], new_options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {**new_options, CONF_SPEED: 7}
