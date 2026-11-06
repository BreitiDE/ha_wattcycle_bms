"""Data update coordinator for WattCycle batteries."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    WattcycleClient,
    WattcycleCommandRejectedError,
    WattcycleError,
    WattcycleNotSupportedError,
    WattcycleSwitchError,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, MANUFACTURER
from .protocol import WattcycleData

_LOGGER = logging.getLogger(__name__)

type WattcycleConfigEntry = ConfigEntry[WattcycleCoordinator]


class WattcycleCoordinator(DataUpdateCoordinator[WattcycleData]):
    """Poll one battery."""

    config_entry: WattcycleConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: WattcycleConfigEntry, client: WattcycleClient
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.address = client.address
        self._info_published = False

    @property
    def device_info(self) -> dr.DeviceInfo:
        """Device registry entry (includes firmware once known)."""
        info = dr.DeviceInfo(
            identifiers={(DOMAIN, self.address)},
            connections={(dr.CONNECTION_BLUETOOTH, self.address)},
            manufacturer=MANUFACTURER,
            model="Smart LiFePO4 (TDT BMS)",
            name=self.config_entry.title,
        )
        if self.data and self.data.info:
            info["sw_version"] = self.data.info.sw_version or None
            info["hw_version"] = self.data.info.hw_version or None
            info["serial_number"] = self.data.info.serial_number or None
        return info

    @property
    def rssi(self) -> int | None:
        """Last advertisement RSSI."""
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return info.rssi if info else None

    def _refresh_ble_device(self) -> None:
        if ble_device := bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        ):
            self.client.set_ble_device(ble_device)

    async def _async_update_data(self) -> WattcycleData:
        self._refresh_ble_device()
        try:
            data = await self.client.async_update()
        except WattcycleNotSupportedError as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="not_supported",
                translation_placeholders={"address": self.address},
            ) from err
        except WattcycleError as err:
            raise UpdateFailed(f"Battery {self.address} not reachable: {err}") from err
        if data.info and not self._info_published and self.data is not None:
            self._publish_device_info(data)
        return data

    async def async_set_switch(self, register: int, on: bool) -> None:
        """Switch a MOSFET and push the confirmed state to all entities."""
        self._refresh_ble_device()
        try:
            status = await self.client.async_set_switch(register, on)
        except WattcycleSwitchError as err:
            await self.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="switch_failed"
            ) from err
        except WattcycleCommandRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_rejected"
            ) from err
        except WattcycleError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        if self.data is not None:
            self.async_set_updated_data(WattcycleClient.replace_status(self.data, status))

    def _publish_device_info(self, data: WattcycleData) -> None:
        assert data.info
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, self.address)}):
            registry.async_update_device(
                device.id,
                sw_version=data.info.sw_version or None,
                hw_version=data.info.hw_version or None,
                serial_number=data.info.serial_number or None,
            )
            self._info_published = True
