"""Base entity for Badge Magic badges."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN
from .coordinator import BadgeCoordinator


class BadgeEntity(Entity):
    """An entity of one badge, updated when the coordinator's state changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BadgeCoordinator, description: EntityDescription) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.address)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
