"""Button entities: power off, reboot, save settings, restore defaults (open firmware)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BadgeConfigEntry, BadgeCoordinator
from .entity import BadgeEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BadgeButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[BadgeCoordinator], Awaitable[None]]


DESCRIPTIONS = (
    BadgeButtonDescription(
        key="power_off",
        translation_key="power_off",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda c: c.async_power_off(),
    ),
    BadgeButtonDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda c: c.async_reboot(),
    ),
    BadgeButtonDescription(
        key="save_config",
        translation_key="save_config",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_save_config(),
    ),
    BadgeButtonDescription(
        key="load_defaults",
        translation_key="load_defaults",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.async_load_defaults(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.has_nextgen:
        async_add_entities(BadgeButton(coordinator, description) for description in DESCRIPTIONS)


class BadgeButton(BadgeEntity, ButtonEntity):
    entity_description: BadgeButtonDescription

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
