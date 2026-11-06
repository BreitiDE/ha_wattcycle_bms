"""Sensors for WattCycle BMS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import WattcycleConfigEntry, WattcycleCoordinator
from .entity import WattcycleEntity
from .protocol import BATTERY_MODES, WattcycleData, alarm_text, state_name

UNIT_AH = "Ah"
PARALLEL_UPDATES = 0

type ValueFn = Callable[[WattcycleData], Any]


@dataclass(frozen=True, kw_only=True)
class WattcycleSensorDescription(SensorEntityDescription):
    """Sensor description with value accessor."""

    value_fn: ValueFn
    attr_fn: Callable[[WattcycleData], dict[str, Any]] | None = None


def _cells(d: WattcycleData) -> tuple[float, ...]:
    return d.cells.cell_voltages


def _min_cell_attrs(d: WattcycleData) -> dict[str, Any]:
    c = _cells(d)
    return {"cell": c.index(min(c)) + 1} if c else {}


def _max_cell_attrs(d: WattcycleData) -> dict[str, Any]:
    c = _cells(d)
    return {"cell": c.index(max(c)) + 1} if c else {}


def _temp_state(d: WattcycleData, index: int) -> str | None:
    states = d.status.temp_states
    return state_name(states[index]) if len(states) > index else None


def _mode(d: WattcycleData) -> str | None:
    if d.status.battery_mode is None:
        return None
    return BATTERY_MODES.get(d.status.battery_mode, "unknown")


def _raw(key: str, fn: ValueFn) -> WattcycleSensorDescription:
    """Raw status byte, for decoding unknown bits."""
    return WattcycleSensorDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=fn,
    )


def _status_byte(index: int) -> ValueFn:
    return lambda d: d.status.status[index] if len(d.status.status) > index else None


SENSORS: tuple[WattcycleSensorDescription, ...] = (
    WattcycleSensorDescription(
        key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.cells.voltage,
    ),
    WattcycleSensorDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.cells.current,
    ),
    WattcycleSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.power,
    ),
    WattcycleSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.cells.soc,
    ),
    WattcycleSensorDescription(
        key="state_of_health",
        translation_key="state_of_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.cells.soh,
    ),
    WattcycleSensorDescription(
        key="remaining_capacity",
        translation_key="remaining_capacity",
        native_unit_of_measurement=UNIT_AH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.cells.remaining_capacity,
    ),
    WattcycleSensorDescription(
        key="full_capacity",
        translation_key="full_capacity",
        native_unit_of_measurement=UNIT_AH,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.cells.full_capacity,
    ),
    WattcycleSensorDescription(
        key="design_capacity",
        translation_key="design_capacity",
        native_unit_of_measurement=UNIT_AH,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.cells.design_capacity,
    ),
    WattcycleSensorDescription(
        key="stored_energy",
        translation_key="stored_energy",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: round(d.cells.remaining_capacity * d.cells.voltage, 1),
    ),
    WattcycleSensorDescription(
        key="cycles",
        translation_key="cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.cells.cycles,
    ),
    WattcycleSensorDescription(
        key="accumulated_capacity",
        translation_key="accumulated_capacity",
        native_unit_of_measurement=UNIT_AH,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.cells.accumulated_capacity,
    ),
    WattcycleSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.cells.remaining_time,
    ),
    WattcycleSensorDescription(
        key="time_to_sleep",
        translation_key="time_to_sleep",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.cells.time_to_sleep,
    ),
    WattcycleSensorDescription(
        key="emergency_time",
        translation_key="emergency_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.cells.emergency_time,
    ),
    WattcycleSensorDescription(
        key="protection_count",
        translation_key="protection_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.cells.protection_count,
    ),
    WattcycleSensorDescription(
        key="balance_current",
        translation_key="balance_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.cells.balance_current,
    ),
    WattcycleSensorDescription(
        key="temperature_mos",
        translation_key="temperature_mos",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        attr_fn=lambda d: {"state": _temp_state(d, 0)},
        value_fn=lambda d: d.cells.temp_mos,
    ),
    WattcycleSensorDescription(
        key="temperature_pcb",
        translation_key="temperature_pcb",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        attr_fn=lambda d: {"state": _temp_state(d, 1)},
        value_fn=lambda d: d.cells.temp_pcb,
    ),
    WattcycleSensorDescription(
        key="cell_voltage_min",
        translation_key="cell_voltage_min",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: min(_cells(d)) if _cells(d) else None,
        attr_fn=_min_cell_attrs,
    ),
    WattcycleSensorDescription(
        key="cell_voltage_max",
        translation_key="cell_voltage_max",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: max(_cells(d)) if _cells(d) else None,
        attr_fn=_max_cell_attrs,
    ),
    WattcycleSensorDescription(
        key="cell_voltage_avg",
        translation_key="cell_voltage_avg",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: round(sum(_cells(d)) / len(_cells(d)), 4) if _cells(d) else None,
    ),
    WattcycleSensorDescription(
        key="cell_voltage_delta",
        translation_key="cell_voltage_delta",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: round((max(_cells(d)) - min(_cells(d))) * 1000) if _cells(d) else None,
    ),
    WattcycleSensorDescription(
        key="battery_mode",
        translation_key="battery_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[*BATTERY_MODES.values(), "unknown"],
        value_fn=_mode,
        attr_fn=lambda d: {"raw": d.status.battery_mode},
    ),
    WattcycleSensorDescription(
        key="alarms",
        translation_key="alarms",
        value_fn=lambda d: None,  # language dependent, see WattcycleSensor.native_value
        attr_fn=lambda d: {
            "count": len(d.status.alarms),
            "protections": d.status.protections,
            "faults": d.status.faults,
            "warnings": d.status.warnings,
        },
    ),
    WattcycleSensorDescription(
        key="problem_code",
        translation_key="problem_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.problem_code,
        attr_fn=lambda d: {"hex": f"0x{d.status.problem_code:04X}"},
    ),
    _raw("status_1", _status_byte(0)),
    _raw("status_2", _status_byte(1)),
    _raw("status_3", _status_byte(2)),
    _raw("status_4", _status_byte(3)),
    _raw("status_5", _status_byte(4)),
    _raw("status_6", _status_byte(5)),
    _raw("status_7", _status_byte(6)),
    _raw("warning_1", lambda d: d.status.warning1),
    _raw("warning_2", lambda d: d.status.warning2),
    _raw("charge_current_state", lambda d: state_name(d.status.charge_current_state)),
    _raw("voltage_state", lambda d: state_name(d.status.voltage_state)),
    _raw("discharge_current_state", lambda d: state_name(d.status.discharge_current_state)),
    WattcycleSensorDescription(
        key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: None,  # replaced in entity
    ),
)


_ALWAYS = frozenset({"rssi", "alarms"})
MAX_STATE_LEN = 255


def _cell_voltage(i: int) -> WattcycleSensorDescription:
    return WattcycleSensorDescription(
        key=f"cell_voltage_{i + 1}",
        translation_key="cell_voltage",
        translation_placeholders={"cell": str(i + 1)},
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.cells.cell_voltages[i] if len(d.cells.cell_voltages) > i else None,
        attr_fn=lambda d: (
            {
                "balancing": d.status.balancing[i],
                "state": state_name(d.status.cell_states[i])
                if len(d.status.cell_states) > i
                else None,
            }
            if d.status.balancing and len(d.status.balancing) > i
            else {}
        ),
    )


def _cell_temp(i: int) -> WattcycleSensorDescription:
    return WattcycleSensorDescription(
        key=f"temperature_cell_{i + 1}",
        translation_key="temperature_cell",
        translation_placeholders={"sensor": str(i + 1)},
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.cells.temp_cells[i] if len(d.cells.temp_cells) > i else None,
        # raw state byte; firmware 1.x reports 0xF1 here (meaning unknown)
        attr_fn=lambda d: {"state": _temp_state(d, i + 2)},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WattcycleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for what this battery actually reports."""
    coordinator = entry.runtime_data
    data = coordinator.data
    descriptions = [d for d in SENSORS if d.key in _ALWAYS or d.value_fn(data) is not None]
    descriptions += [_cell_voltage(i) for i in range(data.cells.cell_count)]
    descriptions += [_cell_temp(i) for i in range(len(data.cells.temp_cells))]
    async_add_entities(WattcycleSensor(coordinator, d) for d in descriptions)


class WattcycleSensor(WattcycleEntity, SensorEntity):
    """A battery value."""

    entity_description: WattcycleSensorDescription

    def __init__(
        self, coordinator: WattcycleCoordinator, description: WattcycleSensorDescription
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator, description)

    @property
    def native_value(self) -> Any:
        """Current value."""
        if self.entity_description.key == "rssi":
            return self.coordinator.rssi
        if self.entity_description.key == "alarms":
            return self._alarm_state()
        return self.entity_description.value_fn(self.coordinator.data)

    def _alarm_state(self) -> str:
        """Active alarms as text, logged by HA on every change."""
        lang = self.hass.config.language if self.hass else "en"
        alarms = self.coordinator.data.status.alarms
        if not alarms:
            return "Keine" if lang.lower().startswith("de") else "None"
        text = ", ".join(alarm_text(a, lang) for a in alarms)
        return text if len(text) <= MAX_STATE_LEN else text[: MAX_STATE_LEN - 1] + "…"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Additional attributes."""
        if fn := self.entity_description.attr_fn:
            return fn(self.coordinator.data)
        return None
