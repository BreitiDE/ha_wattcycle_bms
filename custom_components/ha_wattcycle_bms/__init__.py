"""WattCycle BMS integration for Home Assistant."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .client import WattcycleClient
from .const import (
    CAPACITY_SCALE_AUTO,
    CAPACITY_SCALES,
    CONF_CAPACITY_SCALE,
    CONF_KEEP_ALIVE,
    DEFAULT_KEEP_ALIVE,
    DOMAIN,
)
from .coordinator import WattcycleConfigEntry, WattcycleCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: WattcycleConfigEntry) -> bool:
    """Set up a battery from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"address": address},
        )

    client = WattcycleClient(
        ble_device,
        capacity_scale=CAPACITY_SCALES[entry.options.get(CONF_CAPACITY_SCALE, CAPACITY_SCALE_AUTO)],
        keep_alive=entry.options.get(CONF_KEEP_ALIVE, DEFAULT_KEEP_ALIVE),
    )
    coordinator = WattcycleCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except (ConfigEntryNotReady, ConfigEntryError):
        await client.disconnect()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WattcycleConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.disconnect()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: WattcycleConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
