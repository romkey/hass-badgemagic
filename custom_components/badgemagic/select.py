"""Select entity: the animation mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MODES
from .coordinator import BadgeConfigEntry
from .entity import BadgeEntity

PARALLEL_UPDATES = 1

DESCRIPTION = SelectEntityDescription(key="mode", translation_key="mode", options=MODES)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([BadgeModeSelect(entry.runtime_data, DESCRIPTION)])


class BadgeModeSelect(BadgeEntity, SelectEntity):
    """Mode of message 1; changing it re-uploads the current message."""

    @property
    def current_option(self) -> str:
        return self.coordinator.state.mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_update_settings(mode=option)
