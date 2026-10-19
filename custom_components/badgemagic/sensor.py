"""Sensor entities: battery and firmware version."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_FIRMWARE
from .coordinator import BadgeConfigEntry, BadgeCoordinator
from .entity import BadgeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class BadgeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[BadgeCoordinator], int | str | None]


DESCRIPTIONS = (
    BadgeSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator: coordinator.battery,
    ),
    BadgeSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator: coordinator.entry.data.get(CONF_FIRMWARE),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(BadgeSensor(entry.runtime_data, description) for description in DESCRIPTIONS)


class BadgeSensor(BadgeEntity, SensorEntity):
    """Battery is the last level read; the badge is only read while it advertises."""

    entity_description: BadgeSensorDescription

    @property
    def native_value(self) -> int | str | None:
        return self.entity_description.value_fn(self.coordinator)
