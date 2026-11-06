"""Binary sensors for WattCycle BMS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WattcycleConfigEntry
from .entity import WattcycleEntity
from .protocol import WattcycleData, state_name

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WattcycleBinaryDescription(BinarySensorEntityDescription):
    """Binary sensor description with value accessor."""

    value_fn: Callable[[WattcycleData], bool | None]
    attr_fn: Callable[[WattcycleData], dict[str, Any]] | None = None


def _balancing_cells(d: WattcycleData) -> list[int]:
    return [i + 1 for i, on in enumerate(d.status.balancing or ()) if on]


def _problem_attrs(d: WattcycleData) -> dict[str, Any]:
    s = d.status
    return {
        "protections": s.protections,
        "faults": s.faults,
        "problem_code": f"0x{s.problem_code:04X}",
        "status": [f"0x{b:02X}" for b in s.status],
        "cell_states": [state_name(v) for v in s.cell_states],
        "temperature_states": [state_name(v) for v in s.temp_states],
        "charge_current_state": state_name(s.charge_current_state),
        "voltage_state": state_name(s.voltage_state),
        "discharge_current_state": state_name(s.discharge_current_state),
    }


BINARY_SENSORS: tuple[WattcycleBinaryDescription, ...] = (
    WattcycleBinaryDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: d.cells.current > 0,
    ),
    WattcycleBinaryDescription(
        key="discharging",
        translation_key="discharging",
        value_fn=lambda d: d.cells.current < 0,
    ),
    WattcycleBinaryDescription(
        key="balancing",
        translation_key="balancing",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: any(d.status.balancing) if d.status.balancing is not None else None,
        attr_fn=lambda d: {"cells": _balancing_cells(d)},
    ),
    WattcycleBinaryDescription(
        key="problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.status.problem_active,
        attr_fn=_problem_attrs,
    ),
    WattcycleBinaryDescription(
        key="warning",
        translation_key="warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.status.warning_active,
        attr_fn=lambda d: {
            "warnings": d.status.warnings,
            "warning_1": d.status.warning1,
            "warning_2": d.status.warning2,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattcycleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        WattcycleBinarySensor(coordinator, d)
        for d in BINARY_SENSORS
        if d.value_fn(coordinator.data) is not None
    )


class WattcycleBinarySensor(WattcycleEntity, BinarySensorEntity):
    """A battery state."""

    entity_description: WattcycleBinaryDescription

    @property
    def is_on(self) -> bool | None:
        """State."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Additional attributes."""
        if fn := self.entity_description.attr_fn:
            return fn(self.coordinator.data)
        return None
