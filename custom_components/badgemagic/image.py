"""Image entity: a picture of what Home Assistant last put on the display."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BadgeConfigEntry, BadgeCoordinator
from .entity import BadgeEntity
from .preview import render_png

PARALLEL_UPDATES = 0

DESCRIPTION = ImageEntityDescription(key="preview", translation_key="preview")


async def async_setup_entry(
    hass: HomeAssistant, entry: BadgeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([BadgePreviewImage(entry.runtime_data, DESCRIPTION)])


class BadgePreviewImage(BadgeEntity, ImageEntity):
    """Message 1 of the last upload, or the current streamed frame (updated at most once a second)."""

    _attr_content_type = "image/png"

    def __init__(self, coordinator: BadgeCoordinator, description: ImageEntityDescription) -> None:
        BadgeEntity.__init__(self, coordinator, description)
        ImageEntity.__init__(self, coordinator.hass)
        self._attr_image_last_updated = coordinator.preview_updated
        self._png: bytes | None = None
        self._png_for: object = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_preview_listener(self._async_preview_changed))

    @callback
    def _async_preview_changed(self) -> None:
        self._attr_image_last_updated = self.coordinator.preview_updated
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        bitmap = self.coordinator.preview
        if self._png is None or self._png_for is not bitmap:
            self._png = await self.hass.async_add_executor_job(render_png, bitmap)
            self._png_for = bitmap
        return self._png
