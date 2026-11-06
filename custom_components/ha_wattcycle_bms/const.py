"""Constants for the WattCycle BMS integration."""

from typing import Final

DOMAIN: Final = "ha_wattcycle_bms"
MANUFACTURER: Final = "WattCycle"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_KEEP_ALIVE: Final = "keep_alive"
CONF_CAPACITY_SCALE: Final = "capacity_scale"
CONF_ALLOW_SWITCH_OFF: Final = "allow_switch_off"

DEFAULT_SCAN_INTERVAL: Final = 30  # seconds
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 600
DEFAULT_KEEP_ALIVE: Final = True
DEFAULT_ALLOW_SWITCH_OFF: Final = True  # can be locked in the options

CAPACITY_SCALE_AUTO: Final = "auto"
CAPACITY_SCALES: Final = {CAPACITY_SCALE_AUTO: None, "0.1": 0.1, "1": 1.0}
