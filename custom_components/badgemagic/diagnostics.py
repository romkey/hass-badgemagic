"""Diagnostics for Badge Magic badges."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import BadgeConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BadgeConfigEntry) -> dict[str, Any]:
    return {
        "entry": {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)},
        "badge": entry.runtime_data.diagnostics(),
    }
