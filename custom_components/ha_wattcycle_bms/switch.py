"""Charge / discharge switches for WattCycle BMS.

The switches only send a command when the user (or an automation) operates them.
Their state is always read from the BMS (status 3). Nothing is restored or written
on start-up, so restarting Home Assistant or reloading the integration never
switches the battery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ALLOW_SWITCH_OFF, DEFAULT_ALLOW_SWITCH_OFF, DOMAIN
from .coordinator import WattcycleConfigEntry, WattcycleCoordinator
from .entity import WattcycleEntity
from .protocol import REG_CHARGE_SWITCH, REG_DISCHARGE_SWITCH, WattcycleData

PARALLEL_UPDATES = 1  # one BLE command at a time


@dataclass(frozen=True, kw_only=True)
class WattcycleSwitchDescription(SwitchEntityDescription):
    """Switch description."""

    register: int
    value_fn: Callable[[WattcycleData], bool | None]


SWITCHES: tuple[WattcycleSwitchDescription, ...] = (
    WattcycleSwitchDescription(
        key="charge",
        translation_key="charge",
        device_class=SwitchDeviceClass.SWITCH,
        register=REG_CHARGE_SWITCH,
        value_fn=lambda d: d.status.charge_mosfet,
    ),
    WattcycleSwitchDescription(
        key="discharge",
        translation_key="discharge",
        device_class=SwitchDeviceClass.SWITCH,
        register=REG_DISCHARGE_SWITCH,
        value_fn=lambda d: d.status.discharge_mosfet,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattcycleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        WattcycleSwitch(coordinator, d)
        for d in SWITCHES
        if d.value_fn(coordinator.data) is not None
    )


class WattcycleSwitch(WattcycleEntity, SwitchEntity):
    """Charge or discharge MOSFET switch."""

    entity_description: WattcycleSwitchDescription

    def __init__(
        self, coordinator: WattcycleCoordinator, description: WattcycleSwitchDescription
    ) -> None:
        """Initialize switch."""
        super().__init__(coordinator, description)
        self._allow_off: bool = coordinator.config_entry.options.get(
            CONF_ALLOW_SWITCH_OFF, DEFAULT_ALLOW_SWITCH_OFF
        )

    @property
    def is_on(self) -> bool | None:
        """State as reported by the BMS."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show whether turning off is allowed."""
        return {"switch_off_allowed": self._allow_off}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable charging/discharging."""
        await self.coordinator.async_set_switch(self.entity_description.register, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable charging/discharging (if allowed in the options)."""
        if not self._allow_off:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="switch_off_locked",
                translation_placeholders={"entity": self.entity_id or ""},
            )
        await self.coordinator.async_set_switch(self.entity_description.register, False)
