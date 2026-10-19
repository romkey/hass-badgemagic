"""Switch entities: flash, marquee, Bluetooth and upload settings, and streaming."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_FLASH, CONF_MARQUEE
from .coordinator import BadgeConfigEntry, BadgeCoordinator
from .entity import BadgeEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BadgeSwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[BadgeCoordinator], bool | None]
    set_fn: Callable[[BadgeCoordinator, bool], Awaitable[None]]
    nextgen: bool = False


DESCRIPTIONS = (
    BadgeSwitchDescription(
        key=CONF_FLASH,
        translation_key=CONF_FLASH,
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda c: c.state.flash,
        set_fn=lambda c, on: c.async_update_settings(flash=on),
    ),
    BadgeSwitchDescription(
        key=CONF_MARQUEE,
        translation_key=CONF_MARQUEE,
        entity_category=EntityCategory.CONFIG,
        is_on_fn=lambda c: c.state.marquee,
        set_fn=lambda c, on: c.async_update_settings(marquee=on),
    ),
    BadgeSwitchDescription(
        key="always_on",
        translation_key="always_on",
        entity_category=EntityCategory.CONFIG,
        nextgen=True,
        is_on_fn=lambda c: c.settings.always_on,
        set_fn=lambda c, on: c.async_set_always_on(on),
    ),
    BadgeSwitchDescription(
        key="reset_after_upload",
        translation_key="reset_after_upload",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        nextgen=True,
        is_on_fn=lambda c: c.settings.reset_after_upload,
        set_fn=lambda c, on: c.async_set_reset_after_upload(on),
    ),
    BadgeSwitchDescription(
        key="streaming",
        translation_key="streaming",
        nextgen=True,
        is_on_fn=lambda c: c.streaming,
        set_fn=lambda c, on: c.async_start_streaming() if on else c.async_stop_streaming(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        BadgeSwitch(coordinator, description)
        for description in DESCRIPTIONS
        if coordinator.has_nextgen or not description.nextgen
    )


class BadgeSwitch(BadgeEntity, SwitchEntity):
    """Flash and marquee re-upload the current message; the settings are unknown until set."""

    entity_description: BadgeSwitchDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.set_fn(self.coordinator, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.set_fn(self.coordinator, False)
