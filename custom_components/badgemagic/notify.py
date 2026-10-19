"""Notify entity: send a text message to the badge."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity, NotifyEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BadgeConfigEntry
from .entity import BadgeEntity

PARALLEL_UPDATES = 1

DESCRIPTION = NotifyEntityDescription(key="message", translation_key="message")


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([BadgeNotifyEntity(entry.runtime_data, DESCRIPTION)])


class BadgeNotifyEntity(BadgeEntity, NotifyEntity):
    """Uploads the message as message 1 with the current mode, speed, flash and marquee."""

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        await self.coordinator.async_send_text(message)
