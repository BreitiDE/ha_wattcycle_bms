"""Diagnostics for WattCycle BMS (raw frames help decoding unknown bits)."""

from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .coordinator import WattcycleConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WattcycleConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    adv = bluetooth.async_last_service_info(hass, coordinator.address, connectable=True)
    return {
        "entry": {"data": dict(entry.data), "options": dict(entry.options)},
        "advertisement": (
            {
                "name": adv.name,
                "rssi": adv.rssi,
                "manufacturer_data": {k: v.hex() for k, v in adv.manufacturer_data.items()},
                "service_uuids": adv.service_uuids,
                "source": adv.source,
            }
            if adv
            else None
        ),
        "client": coordinator.client.diagnostics(),
        "data": coordinator.data.as_dict() if coordinator.data else None,
    }
