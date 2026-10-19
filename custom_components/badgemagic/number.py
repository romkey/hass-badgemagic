"""Number entities: animation speed and brightness."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import BRIGHTNESS_STEPS, MAX_SPEED, MIN_SPEED
from .coordinator import BadgeConfigEntry
from .entity import BadgeEntity

PARALLEL_UPDATES = 1

SPEED = NumberEntityDescription(
    key="speed",
    translation_key="speed",
    native_min_value=MIN_SPEED,
    native_max_value=MAX_SPEED,
    native_step=1,
    mode=NumberMode.SLIDER,
    entity_category=EntityCategory.CONFIG,
)

BRIGHTNESS = NumberEntityDescription(
    key="brightness",
    translation_key="brightness",
    native_min_value=BRIGHTNESS_STEPS[0],
    native_max_value=BRIGHTNESS_STEPS[-1],
    native_step=BRIGHTNESS_STEPS[1] - BRIGHTNESS_STEPS[0],
    native_unit_of_measurement=PERCENTAGE,
    mode=NumberMode.SLIDER,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([BadgeSpeedNumber(coordinator, SPEED), BadgeBrightnessNumber(coordinator, BRIGHTNESS)])


class BadgeSpeedNumber(BadgeEntity, NumberEntity):
    """Speed 1 (slowest) to 8; changing it re-uploads the current message."""

    @property
    def native_value(self) -> int:
        return self.coordinator.state.speed

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_update_settings(speed=int(value))


class BadgeBrightnessNumber(BadgeEntity, NumberEntity):
    """25 to 100 %. Unknown until set from Home Assistant: the badge cannot be read back.

    Open firmware applies it at once and saves it. OEM firmware takes it with
    the next upload, so the current message is re-uploaded.
    """

    @property
    def native_value(self) -> int | None:
        return self.coordinator.settings.brightness

    async def async_set_native_value(self, value: float) -> None:
        # Snap to the nearest of the four steps.
        percent = min(BRIGHTNESS_STEPS, key=lambda step: abs(step - value))
        await self.coordinator.async_set_brightness(percent)
