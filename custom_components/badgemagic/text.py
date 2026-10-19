"""Text entities: the message shown on the badge, and the badge's Bluetooth name."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MAX_TEXT_LENGTH
from .coordinator import BadgeConfigEntry
from .entity import BadgeEntity

PARALLEL_UPDATES = 1

MAX_NAME_BYTES = 20

MESSAGE = TextEntityDescription(key="text", translation_key="message", native_max=MAX_TEXT_LENGTH)
DEVICE_NAME = TextEntityDescription(
    key="device_name",
    translation_key="device_name",
    native_min=1,
    native_max=MAX_NAME_BYTES,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[TextEntity] = [BadgeTextEntity(coordinator, MESSAGE)]
    if coordinator.has_nextgen:
        entities.append(BadgeNameEntity(coordinator, DEVICE_NAME))
    async_add_entities(entities)


class BadgeTextEntity(BadgeEntity, TextEntity):
    """The last message Home Assistant sent; setting it uploads the new text."""

    @property
    def native_value(self) -> str | None:
        return self.coordinator.state.text

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_send_text(value)


class BadgeNameEntity(BadgeEntity, TextEntity):
    """The badge's Bluetooth name; the badge advertises a new name after it restarts."""

    @property
    def native_value(self) -> str | None:
        return self.coordinator.settings.device_name or self.coordinator.name

    async def async_set_value(self, value: str) -> None:
        name = value.strip()
        if not name or len(name.encode("utf-8")) > MAX_NAME_BYTES:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_name")
        await self.coordinator.async_set_device_name(name)
