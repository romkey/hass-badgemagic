"""Config flow for Badge Magic badges."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from bleak.exc import BleakError
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .badgemagic_ble import BadgeClient, BadgeError
from .const import (
    CONF_FIRMWARE,
    CONF_FLASH,
    CONF_FONT,
    CONF_HARDWARE,
    CONF_HAS_NEXTGEN,
    CONF_KEEP_BLUETOOTH,
    CONF_MANUFACTURER,
    CONF_MARQUEE,
    CONF_MODE,
    CONF_MODEL,
    CONF_SET_CLOCK,
    CONF_SPEED,
    CONF_WIDE_TEXT,
    DEFAULT_OPTIONS,
    DOMAIN,
    FONTS,
    MAX_SPEED,
    MIN_SPEED,
    MODES,
    SERVICE_UUID,
    WIDE_TEXT_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    """The badge could not be reached or read."""


def _is_badge(service_info: BluetoothServiceInfoBleak) -> bool:
    return SERVICE_UUID in (uuid.lower() for uuid in service_info.service_uuids)


def _title(service_info: BluetoothServiceInfoBleak) -> str:
    return service_info.name or service_info.address


class BadgeMagicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a badge found over Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BadgeMagicOptionsFlow:
        return BadgeMagicOptionsFlow()

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._discovery_info is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                return await self._async_create(self._discovery_info)
            except CannotConnect:
                errors["base"] = "cannot_connect"
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": _title(self._discovery_info)},
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            try:
                return await self._async_create(self._discovered[address])
            except CannotConnect:
                errors["base"] = "cannot_connect"

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in current and _is_badge(info):
                self._discovered.setdefault(info.address, info)
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {address: f"{_title(info)} ({address})" for address, info in self._discovered.items()}
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _async_create(self, service_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Read device info once, then create the entry. Raises CannotConnect."""
        data = await self._async_read_badge(service_info)
        return self.async_create_entry(
            title=_title(service_info),
            data={CONF_ADDRESS: service_info.address, **data},
            options=dict(DEFAULT_OPTIONS),
        )

    async def _async_read_badge(self, service_info: BluetoothServiceInfoBleak) -> dict[str, Any]:
        device = async_ble_device_from_address(self.hass, service_info.address, connectable=True) or service_info.device
        try:
            async with BadgeClient(device) as client:
                info = await client.read_device_info()
                has_nextgen = client.has_nextgen
        except (BadgeError, BleakError, TimeoutError) as err:
            _LOGGER.debug("Could not read %s: %s", service_info.address, err)
            raise CannotConnect from err
        return {
            CONF_MODEL: info.model,
            CONF_FIRMWARE: info.firmware_revision,
            CONF_HARDWARE: info.hardware_revision,
            CONF_MANUFACTURER: info.manufacturer,
            CONF_HAS_NEXTGEN: has_nextgen,
        }


def _select(options: list[str], key: str) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN, translation_key=key))


class BadgeMagicOptionsFlow(OptionsFlow):
    """Defaults for messages sent to this badge."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_SPEED] = int(user_input[CONF_SPEED])
            return self.async_create_entry(data=user_input)

        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_FONT, default=current[CONF_FONT]): _select(FONTS, CONF_FONT),
                vol.Required(CONF_MODE, default=current[CONF_MODE]): _select(MODES, CONF_MODE),
                vol.Required(CONF_SPEED, default=current[CONF_SPEED]): NumberSelector(
                    NumberSelectorConfig(min=MIN_SPEED, max=MAX_SPEED, step=1, mode=NumberSelectorMode.SLIDER)
                ),
                vol.Required(CONF_FLASH, default=current[CONF_FLASH]): BooleanSelector(),
                vol.Required(CONF_MARQUEE, default=current[CONF_MARQUEE]): BooleanSelector(),
                vol.Required(CONF_WIDE_TEXT, default=current[CONF_WIDE_TEXT]): _select(
                    WIDE_TEXT_OPTIONS, CONF_WIDE_TEXT
                ),
                vol.Required(CONF_SET_CLOCK, default=current[CONF_SET_CLOCK]): BooleanSelector(),
            }
        )
        if self.config_entry.data.get(CONF_HAS_NEXTGEN):
            schema = schema.extend(
                {vol.Required(CONF_KEEP_BLUETOOTH, default=current[CONF_KEEP_BLUETOOTH]): BooleanSelector()}
            )
        return self.async_show_form(step_id="init", data_schema=schema)
