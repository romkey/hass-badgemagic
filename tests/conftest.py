"""Fixtures for the Badge Magic integration tests.

``BadgeClient`` is always mocked; no Bluetooth hardware is used. Badges
"advertise" by injecting advertisements into Home Assistant's Bluetooth
manager, so ``async_ble_device_from_address`` and discovery behave as they
do on a real instance.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.badgemagic.badgemagic_ble import DeviceInfo
from custom_components.badgemagic.const import (
    CONF_FIRMWARE,
    CONF_HARDWARE,
    CONF_HAS_NEXTGEN,
    CONF_MANUFACTURER,
    CONF_MODEL,
    DEFAULT_OPTIONS,
    DOMAIN,
    SERVICE_UUID,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
NAME = "LED Badge Magic"
FIRMWARE = "(C) v0.1-68-g00ef45a-dirty"

DEVICE_INFO = DeviceInfo(
    model="BM1144-C",
    firmware_revision=FIRMWARE,
    hardware_revision="REV3",
    manufacturer="FOSSASIA",
)

ENTRY_DATA = {
    CONF_ADDRESS: ADDRESS,
    CONF_MODEL: "BM1144-C",
    CONF_FIRMWARE: FIRMWARE,
    CONF_HARDWARE: "REV3",
    CONF_MANUFACTURER: "FOSSASIA",
    CONF_HAS_NEXTGEN: True,
}


def make_service_info(
    address: str = ADDRESS,
    name: str = NAME,
    service_uuids: list[str] | None = None,
    service_data: dict[str, bytes] | None = None,
) -> BluetoothServiceInfoBleak:
    uuids = [SERVICE_UUID] if service_uuids is None else service_uuids
    service_data = service_data or {}
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=uuids,
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=AdvertisementData(
            local_name=name,
            manufacturer_data={},
            service_data=service_data,
            service_uuids=uuids,
            tx_power=-127,
            rssi=-60,
            platform_data=(),
        ),
        time=time.monotonic(),
        connectable=True,
        tx_power=-127,
    )


def inject_advertisement(hass: HomeAssistant, service_info: BluetoothServiceInfoBleak | None = None) -> None:
    """Make Home Assistant see the badge advertising."""
    bluetooth.async_get_advertisement_callback(hass)(service_info or make_service_info())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
async def setup_bluetooth(hass: HomeAssistant, mock_bluetooth: None) -> None:
    """Run Home Assistant's Bluetooth integration with mocked adapters."""
    assert await async_setup_component(hass, "bluetooth", {})
    await hass.async_block_till_done()


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """The BadgeClient used by the config flow and the coordinator."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.upload = AsyncMock()
    client.read_battery = AsyncMock(return_value=84)
    client.read_device_info = AsyncMock(return_value=DEVICE_INFO)
    client.has_nextgen = True
    client.is_connected = True
    for method in (
        "set_brightness",
        "set_always_on",
        "set_name",
        "set_reset_after_upload",
        "set_splash",
        "set_splash_speed",
        "save_config",
        "load_defaults",
        "power_off",
        "reboot",
        "start_streaming",
        "stop_streaming",
        "stream_frame",
    ):
        setattr(client, method, AsyncMock())
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    with (
        patch("custom_components.badgemagic.coordinator.BadgeClient", return_value=client) as client_class,
        patch("custom_components.badgemagic.config_flow.BadgeClient", new=client_class),
    ):
        client.client_class = client_class
        yield client


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        unique_id=ADDRESS,
        data=dict(ENTRY_DATA),
        options=dict(DEFAULT_OPTIONS),
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def oem_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A badge on OEM firmware: no next-gen service."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="LSLED",
        unique_id=ADDRESS,
        data={**ENTRY_DATA, CONF_HAS_NEXTGEN: False, CONF_MODEL: None, CONF_FIRMWARE: None},
        options=dict(DEFAULT_OPTIONS),
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_oem_entry(hass: HomeAssistant, oem_entry: MockConfigEntry, mock_client: MagicMock) -> MockConfigEntry:
    inject_advertisement(hass, make_service_info(name="LSLED"))
    assert await hass.config_entries.async_setup(oem_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.connect.reset_mock()
    mock_client.disconnect.reset_mock()
    mock_client.read_battery.reset_mock()
    return oem_entry


@pytest.fixture
def no_sleep() -> Generator[AsyncMock]:
    """Make the coordinator's stream and reconnect delays instant."""
    real_sleep = asyncio.sleep

    async def instant(delay: float) -> None:
        await real_sleep(0)

    with patch("custom_components.badgemagic.coordinator._sleep", side_effect=instant) as sleep:
        yield sleep


@pytest.fixture
async def setup_entry(hass: HomeAssistant, config_entry: MockConfigEntry, mock_client: MagicMock) -> MockConfigEntry:
    """A loaded badge that is advertising; the startup battery read has finished."""
    inject_advertisement(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.connect.reset_mock()
    mock_client.disconnect.reset_mock()
    mock_client.read_battery.reset_mock()
    return config_entry
