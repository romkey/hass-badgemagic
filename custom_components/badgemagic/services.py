"""Badge Magic actions: send_messages, set_splash_screen, stream_frame, stream_text."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .badgemagic_ble import Bitmap, Message, Xbm, load_image, parse_xbm
from .badgemagic_ble.protocol.ng import SPLASH_MAX_BITS
from .const import (
    ALIGNMENTS,
    ATTR_ALIGN,
    ATTR_DURATION,
    ATTR_EFFECT,
    ATTR_FPS,
    ATTR_FRAME_HEIGHT,
    ATTR_IMAGE,
    ATTR_MESSAGES,
    ATTR_PIN,
    ATTR_SPEED_MS,
    ATTR_STOP_AFTER,
    ATTR_TEXT,
    CONF_FLASH,
    CONF_FONT,
    CONF_MARQUEE,
    CONF_MODE,
    CONF_SET_CLOCK,
    CONF_SPEED,
    DEFAULT_FPS,
    DOMAIN,
    EFFECT_AUTO,
    EFFECTS,
    FONTS,
    MAX_FPS,
    MAX_MESSAGES,
    MAX_SPEED,
    MIN_FPS,
    MIN_SPEED,
    MODES,
    SERVICE_SEND_MESSAGES,
    SERVICE_SET_SPLASH_SCREEN,
    SERVICE_STREAM_FRAME,
    SERVICE_STREAM_TEXT,
)
from .coordinator import BadgeConfigEntry, BadgeCoordinator, mode_from_name

MEDIA_SOURCE_PREFIX = "media-source://"


def _text_or_image(value: dict[str, Any]) -> dict[str, Any]:
    if (ATTR_TEXT in value) == (ATTR_IMAGE in value):
        raise vol.Invalid("each message needs exactly one of 'text' or 'image'")
    return value


MESSAGE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_TEXT): cv.string,
            vol.Optional(ATTR_IMAGE): cv.string,
            vol.Optional(CONF_MODE): vol.In(MODES),
            vol.Optional(CONF_SPEED): vol.All(vol.Coerce(int), vol.Range(min=MIN_SPEED, max=MAX_SPEED)),
            vol.Optional(CONF_FLASH): cv.boolean,
            vol.Optional(CONF_MARQUEE): cv.boolean,
            vol.Optional(CONF_FONT): vol.In(FONTS),
        }
    ),
    _text_or_image,
)

SEND_MESSAGES_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ENTITY_ID): cv.comp_entity_ids,
        vol.Required(ATTR_MESSAGES): vol.All(cv.ensure_list, vol.Length(min=1, max=MAX_MESSAGES), [MESSAGE_SCHEMA]),
        vol.Optional(ATTR_PIN): vol.All(cv.string, vol.Match(r"^\d{4}$", msg="the PIN is 4 digits")),
        vol.Optional(CONF_SET_CLOCK): cv.boolean,
    }
)


TARGET_SCHEMA = {
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_ENTITY_ID): cv.comp_entity_ids,
}

SET_SPLASH_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Required(ATTR_IMAGE): cv.string,
        vol.Optional(ATTR_FRAME_HEIGHT, default=11): vol.All(vol.Coerce(int), vol.Range(min=1, max=255)),
        vol.Optional(ATTR_SPEED_MS): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    }
)

STREAM_FRAME_SCHEMA = vol.All(
    vol.Schema(
        {
            **TARGET_SCHEMA,
            vol.Optional(ATTR_TEXT): cv.string,
            vol.Optional(ATTR_IMAGE): cv.string,
            vol.Optional(CONF_FONT): vol.In(FONTS),
            vol.Optional(ATTR_ALIGN, default="center"): vol.In(ALIGNMENTS),
        }
    ),
    _text_or_image,
)

STREAM_TEXT_SCHEMA = vol.Schema(
    {
        **TARGET_SCHEMA,
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(CONF_FONT): vol.In(FONTS),
        vol.Optional(ATTR_EFFECT, default=EFFECT_AUTO): vol.In(EFFECTS),
        vol.Optional(ATTR_FPS, default=DEFAULT_FPS): vol.All(vol.Coerce(float), vol.Range(min=MIN_FPS, max=MAX_FPS)),
        vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(float), vol.Range(min=0, max=86400)),
        vol.Optional(ATTR_STOP_AFTER, default=False): cv.boolean,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGES, _async_send_messages, schema=SEND_MESSAGES_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_SPLASH_SCREEN, _async_set_splash, schema=SET_SPLASH_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STREAM_FRAME, _async_stream_frame, schema=STREAM_FRAME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_STREAM_TEXT, _async_stream_text, schema=STREAM_TEXT_SCHEMA)


def _require_nextgen(coordinator: BadgeCoordinator) -> None:
    if not coordinator.has_nextgen:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="nextgen_required",
            translation_placeholders={"name": coordinator.name},
        )


async def _async_set_splash(call: ServiceCall) -> None:
    coordinators = await _async_targets(call)
    for coordinator in coordinators:
        _require_nextgen(coordinator)
    xbm = await _async_load_splash(call.hass, call.data[ATTR_IMAGE])
    for coordinator in coordinators:
        await coordinator.async_set_splash(xbm, call.data[ATTR_FRAME_HEIGHT], call.data.get(ATTR_SPEED_MS))


async def _async_stream_frame(call: ServiceCall) -> None:
    hass = call.hass
    coordinators = await _async_targets(call)
    for coordinator in coordinators:
        _require_nextgen(coordinator)
    image = await _async_load_image(hass, call.data[ATTR_IMAGE]) if ATTR_IMAGE in call.data else None
    for coordinator in coordinators:
        if image is not None:
            bitmap = image
        else:
            bitmap = await coordinator.async_render_text(call.data[ATTR_TEXT], call.data.get(CONF_FONT))
        await coordinator.async_stream_frame(bitmap, call.data[ATTR_ALIGN])


async def _async_stream_text(call: ServiceCall) -> None:
    coordinators = await _async_targets(call)
    for coordinator in coordinators:
        _require_nextgen(coordinator)
    for coordinator in coordinators:
        await coordinator.async_stream_text(
            call.data[ATTR_TEXT],
            font=call.data.get(CONF_FONT),
            effect=call.data[ATTR_EFFECT],
            fps=call.data[ATTR_FPS],
            duration=call.data.get(ATTR_DURATION),
            stop_after=call.data[ATTR_STOP_AFTER],
        )


async def _async_send_messages(call: ServiceCall) -> None:
    hass = call.hass
    coordinators = await _async_targets(call)
    for coordinator in coordinators:
        messages = [await _async_build_message(hass, coordinator, item) for item in call.data[ATTR_MESSAGES]]
        await coordinator.async_upload(
            messages,
            pin=call.data.get(ATTR_PIN),
            set_clock=call.data.get(CONF_SET_CLOCK),
        )


def _target_entry_ids(call: ServiceCall) -> list[str]:
    """Config entries of the targeted badge devices or entities, in a stable order."""
    dev_reg = dr.async_get(call.hass)
    ent_reg = er.async_get(call.hass)
    entry_ids: dict[str, None] = {}
    for device_id in cv.ensure_list(call.data.get(ATTR_DEVICE_ID)):
        if (device := dev_reg.async_get(device_id)) is not None:
            entry_ids.update(dict.fromkeys(device.config_entries))
    for entity_id in cv.ensure_list(call.data.get(ATTR_ENTITY_ID)):
        if (entity := ent_reg.async_get(entity_id)) is not None and entity.config_entry_id:
            entry_ids[entity.config_entry_id] = None
    return list(entry_ids)


async def _async_targets(call: ServiceCall) -> list[BadgeCoordinator]:
    entry_ids = _target_entry_ids(call)
    coordinators: list[BadgeCoordinator] = []
    for entry_id in entry_ids:
        entry: BadgeConfigEntry | None = call.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"name": entry.title},
            )
        coordinators.append(entry.runtime_data)
    if not coordinators:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_target")
    return coordinators


async def _async_build_message(hass: HomeAssistant, coordinator: BadgeCoordinator, item: dict[str, Any]) -> Message:
    """One upload message; fields the item leaves out come from the badge's options."""
    mode = item.get(CONF_MODE, coordinator.option(CONF_MODE))
    speed = item.get(CONF_SPEED, coordinator.option(CONF_SPEED))
    flash = item.get(CONF_FLASH, coordinator.option(CONF_FLASH))
    marquee = item.get(CONF_MARQUEE, coordinator.option(CONF_MARQUEE))
    if ATTR_TEXT in item:
        return await coordinator.async_text_message(
            item[ATTR_TEXT], mode=mode, speed=speed, flash=flash, marquee=marquee, font=item.get(CONF_FONT)
        )
    bitmap = await _async_load_image(hass, item[ATTR_IMAGE])
    return Message(bitmap, mode=mode_from_name(mode), speed=speed, flash=flash, marquee=marquee)


async def _async_image_path(hass: HomeAssistant, source: str) -> Path:
    if source.startswith(MEDIA_SOURCE_PREFIX):
        return await _async_resolve_media(hass, source)
    path = Path(source)
    if not hass.config.is_allowed_path(str(path)):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="path_not_allowed",
            translation_placeholders={"path": source},
        )
    return path


def _read_splash(path: Path) -> Xbm:
    if path.suffix.lower() == ".xbm":
        return parse_xbm(path.read_text())
    return Xbm.from_image(path)


async def _async_load_splash(hass: HomeAssistant, source: str) -> Xbm:
    """An image (scaled down to fit 48x44) or .xbm file for the boot splash."""
    path = await _async_image_path(hass, source)
    try:
        xbm = await hass.async_add_executor_job(_read_splash, path)
    except (OSError, ValueError) as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="image_unreadable",
            translation_placeholders={"path": source, "error": str(err)},
        ) from err
    if len(xbm.bits) > SPLASH_MAX_BITS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="splash_too_large",
            translation_placeholders={
                "path": source,
                "width": str(xbm.width),
                "height": str(xbm.height),
                "bytes": str(len(xbm.bits)),
                "max": str(SPLASH_MAX_BITS),
            },
        )
    return xbm


async def _async_load_image(hass: HomeAssistant, source: str) -> Bitmap:
    path = await _async_image_path(hass, source)
    try:
        bitmap = await hass.async_add_executor_job(load_image, path)
    except (OSError, ValueError) as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="image_unreadable",
            translation_placeholders={"path": source, "error": str(err)},
        ) from err
    if bitmap.width == 0:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="image_unreadable",
            translation_placeholders={"path": source, "error": "empty image"},
        )
    return bitmap


async def _async_resolve_media(hass: HomeAssistant, media_id: str) -> Path:
    """Local file behind a media-source URI; only local media is supported."""
    if "media_source" not in hass.config.components:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="media_not_local",
            translation_placeholders={"path": media_id},
        )
    from homeassistant.components import media_source  # noqa: PLC0415

    try:
        media = await media_source.async_resolve_media(hass, media_id, None)
    except media_source.Unresolvable as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="image_unreadable",
            translation_placeholders={"path": media_id, "error": str(err)},
        ) from err
    if media.path is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="media_not_local",
            translation_placeholders={"path": media_id},
        )
    return media.path
