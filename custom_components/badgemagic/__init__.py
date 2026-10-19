"""Badge Magic LED badges over Bluetooth LE."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import CONF_FIRMWARE, CONF_HARDWARE, CONF_MANUFACTURER, CONF_MODEL, DOMAIN
from .coordinator import BadgeConfigEntry, BadgeCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.NOTIFY,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BadgeConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS]
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, address)},
        identifiers={(DOMAIN, address)},
        name=entry.title,
        manufacturer=entry.data.get(CONF_MANUFACTURER) or "FOSSASIA",
        model=entry.data.get(CONF_MODEL),
        sw_version=entry.data.get(CONF_FIRMWARE),
        hw_version=entry.data.get(CONF_HARDWARE),
    )

    coordinator = BadgeCoordinator(hass, entry, address)
    await coordinator.async_start()
    entry.async_on_unload(coordinator.async_shutdown)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BadgeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BadgeConfigEntry) -> None:
    """Forget the remembered state when the badge is removed."""
    coordinator = BadgeCoordinator(hass, entry, entry.data[CONF_ADDRESS])
    await coordinator.async_remove_store()
    ir.async_delete_issue(hass, DOMAIN, coordinator.issue_id)
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])
