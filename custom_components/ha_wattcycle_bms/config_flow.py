"""Config flow for WattCycle BMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import (
    CAPACITY_SCALE_AUTO,
    CAPACITY_SCALES,
    CONF_ALLOW_SWITCH_OFF,
    CONF_CAPACITY_SCALE,
    CONF_KEEP_ALIVE,
    CONF_SCAN_INTERVAL,
    DEFAULT_ALLOW_SWITCH_OFF,
    DEFAULT_KEEP_ALIVE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import WattcycleConfigEntry
from .protocol import is_wattcycle


def _is_wattcycle(info: BluetoothServiceInfoBleak) -> bool:
    return is_wattcycle(info.name, set(info.manufacturer_data), info.service_uuids)


class WattcycleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._devices: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not _is_wattcycle(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered battery."""
        assert self._discovery
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery.name,
                data={CONF_ADDRESS: self._discovery.address},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._discovery.name},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a battery from the discovered ones."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._devices.get(address, address).split(" (")[0]
            return self.async_create_entry(title=name, data={CONF_ADDRESS: address})

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured or not _is_wattcycle(info):
                continue
            self._devices[info.address] = f"{info.name} ({info.address})"
        if not self._devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._devices)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: WattcycleConfigEntry) -> OptionsFlow:
        """Options flow."""
        return WattcycleOptionsFlow()


class WattcycleOptionsFlow(OptionsFlow):
    """Polling and decoding options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_KEEP_ALIVE, default=opts.get(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE)
                ): bool,
                vol.Required(
                    CONF_ALLOW_SWITCH_OFF,
                    default=opts.get(CONF_ALLOW_SWITCH_OFF, DEFAULT_ALLOW_SWITCH_OFF),
                ): bool,
                vol.Required(
                    CONF_CAPACITY_SCALE,
                    default=opts.get(CONF_CAPACITY_SCALE, CAPACITY_SCALE_AUTO),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(CAPACITY_SCALES),
                        translation_key=CONF_CAPACITY_SCALE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
