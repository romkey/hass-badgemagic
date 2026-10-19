"""Binary sensors: whether Home Assistant can reach the badge, and whether it is streaming."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BadgeConfigEntry, BadgeCoordinator
from .entity import BadgeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BadgeBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[BadgeCoordinator], bool]
    nextgen: bool = False


DESCRIPTIONS = (
    BadgeBinarySensorDescription(
        key="reachable",
        translation_key="reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda c: c.reachable,
    ),
    BadgeBinarySensorDescription(
        key="streaming_active",
        translation_key="streaming_active",
        entity_category=EntityCategory.DIAGNOSTIC,
        nextgen=True,
        is_on_fn=lambda c: c.streaming,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        BadgeBinarySensor(coordinator, description)
        for description in DESCRIPTIONS
        if coordinator.has_nextgen or not description.nextgen
    )


class BadgeBinarySensor(BadgeEntity, BinarySensorEntity):
    """Reachable is on while the badge advertises (BT-PAIRING mode or always-on Bluetooth)."""

    entity_description: BadgeBinarySensorDescription

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.coordinator)
