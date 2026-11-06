"""Pure protocol layer for WattCycle batteries (TDT smart BMS).

No Bluetooth, no Home Assistant: frame building, frame reassembly and payload
decoding only, so everything here is unit-testable with recorded frames.

Derived from the TDT plugin of aiobmsble (https://github.com/patman15/aiobmsble),
Copyright the aiobmsble authors, licensed under Apache-2.0. Modified: standalone
implementation, register/function framing, write commands, alarm decoding
(bit meanings as used by the official WattCycle app).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import re
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

# --- BLE layout -------------------------------------------------------------
SERVICE_UUID: Final = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID: Final = "0000fff1-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID: Final = "0000fff2-0000-1000-8000-00805f9b34fb"
CHAR_UNLOCK_UUID: Final = "0000fffa-0000-1000-8000-00805f9b34fb"
UNLOCK_KEY: Final = b"HiLink"
UNLOCK_ACK: Final = 0x01

# --- framing ----------------------------------------------------------------
# [HEAD, VERSION, DEV_ADDR, FUNCTION, REG_HI, REG_LO, LEN_HI, LEN_LO, DATA.., CRC_HI, CRC_LO, 0x0D]
HEAD_STD: Final = 0x7E  # response head, command head on older units
HEAD_ALT: Final = 0x1E  # command head on newer units
CMD_HEADS: Final = (HEAD_ALT, HEAD_STD)  # probing order
TAIL: Final = 0x0D
FRAME_VERSION: Final = 0x00  # "old" framing, understood by all units
RSP_VERSIONS: Final = frozenset({0x00, 0x01, 0x04})
DEVICE_ADDRESS: Final = 0x01
FUNC_READ: Final = 0x03
FUNC_WRITE: Final = 0x06
FUNC_ERROR_FLAG: Final = 0x80  # 0x83 / 0x86: request rejected
HEADER_LEN: Final = 8
TRAILER_LEN: Final = 3  # CRC (2) + tail (1)
MAX_PAYLOAD: Final = 0x200

# registers
REG_CELL_DATA: Final = 0x8C  # 140 analog quantities
REG_STATUS: Final = 0x8D  # 141 warning info
REG_DEVICE_INFO: Final = 0x92  # 146 product info
REG_CHARGE_SWITCH: Final = 0x82  # 130
REG_DISCHARGE_SWITCH: Final = 0x83  # 131
# backwards compatible names
CMD_CELL_DATA: Final = REG_CELL_DATA
CMD_STATUS: Final = REG_STATUS
CMD_DEVICE_INFO: Final = REG_DEVICE_INFO
READ_COMMANDS: Final = frozenset({REG_CELL_DATA, REG_STATUS, REG_DEVICE_INFO})

TEMP_OFFSET: Final = 2730  # 0.1 K -> 0.1 degC, as used by the vendor app

# --- advertisement matching ---------------------------------------------------
# The vendor app accepts names starting with "WT" or "XDZN" (WTDH04SC02,
# WTaHdAZ2603312531, WTbFcBG2621080, XDZN_001_49A1) ...
_NAME_PATTERN: Final = re.compile(r"^(WT|XDZN).+", re.IGNORECASE)
# ... and older units advertise manufacturer id 54976 (really the MAC prefix C0:D6).
WATTCYCLE_MANUFACTURER_IDS: Final = frozenset({54976})
# Same names are used for packs with JK or JBD BMS - those are not TDT and excluded.
JK_MANUFACTURER_ID: Final = 2917
JBD_MANUFACTURER_ID: Final = 8192
JBD_SERVICE_UUID: Final = "0000ff00-0000-1000-8000-00805f9b34fb"
FOREIGN_BMS_MANUFACTURER_IDS: Final = frozenset({JK_MANUFACTURER_ID, JBD_MANUFACTURER_ID})


def is_wattcycle(
    name: str | None,
    manufacturer_ids: set[int] | frozenset[int] = frozenset(),
    service_uuids: list[str] | None = None,
) -> bool:
    """Return True if an advertisement belongs to a WattCycle battery with TDT BMS."""
    ids = set(manufacturer_ids)
    if ids & FOREIGN_BMS_MANUFACTURER_IDS:
        return False
    uuids = {u.lower() for u in service_uuids or ()}
    if JBD_SERVICE_UUID in uuids and SERVICE_UUID not in uuids:
        return False
    if name and _NAME_PATTERN.match(name):
        return True
    return bool(WATTCYCLE_MANUFACTURER_IDS & ids)


# --- helpers ----------------------------------------------------------------
def crc_modbus(data: bytes | bytearray) -> int:
    """CRC-16/MODBUS (transmitted big-endian by this BMS)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def build_command(
    register: int,
    data: bytes = b"",
    head: int = HEAD_ALT,
    function: int = FUNC_READ,
    version: int = FRAME_VERSION,
) -> bytes:
    """Assemble a request frame."""
    if head not in CMD_HEADS:
        raise ValueError(f"invalid command head 0x{head:02X}")
    if function not in (FUNC_READ, FUNC_WRITE):
        raise ValueError(f"invalid function 0x{function:02X}")
    frame = bytearray((head, version, DEVICE_ADDRESS, function))
    frame += register.to_bytes(2, "big") + len(data).to_bytes(2, "big") + data
    frame += crc_modbus(frame).to_bytes(2, "big") + bytes((TAIL,))
    return bytes(frame)


def build_write(
    register: int, data: bytes, head: int = HEAD_ALT, version: int = FRAME_VERSION
) -> bytes:
    """Assemble a write frame (function 0x06), like the app's buildWriteFrame."""
    if not data:
        raise ValueError("write needs data")
    return build_command(register, data, head, FUNC_WRITE, version)


def decode_current(raw: int) -> float:
    """Decode a signed current word.

    bit 15: negative (discharge), bit 14: value in 0.1 A, bits 0..13: magnitude.
    """
    value = float(raw & 0x3FFF)
    if raw & 0x4000:
        value /= 10
    return -value if raw & 0x8000 else value


def _b2str(raw: bytes) -> str:
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    return "".join(c for c in text if c.isprintable()).strip()


def _u16(buf: bytes, pos: int) -> int | None:
    return int.from_bytes(buf[pos : pos + 2], "big") if len(buf) >= pos + 2 else None


def _u32(buf: bytes, pos: int, signed: bool = False) -> int | None:
    if len(buf) < pos + 4:
        return None
    return int.from_bytes(buf[pos : pos + 4], "big", signed=signed)


def _u8(buf: bytes, pos: int) -> int | None:
    return buf[pos] if len(buf) > pos else None


# --- frames -----------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Frame:
    """A validated response frame."""

    version: int
    device_address: int
    function: int
    register: int
    payload: bytes
    raw: bytes
    crc_ok: bool = True

    @property
    def cmd(self) -> int:
        """Register address (name kept from 0.1.0)."""
        return self.register

    @property
    def is_error(self) -> bool:
        """BMS answered with an exception function code (0x83/0x86)."""
        return bool(self.function & FUNC_ERROR_FLAG)


class FrameAssembler:
    """Reassemble notification chunks into frames."""

    def __init__(self) -> None:
        """Initialize empty buffer."""
        self._buf = bytearray()

    def reset(self) -> None:
        """Drop buffered data."""
        self._buf.clear()

    def feed(self, chunk: bytes | bytearray) -> list[Frame]:
        """Add a notification chunk, return all complete frames."""
        # A chunk that carries a valid-looking header starts a new frame and
        # discards a stale partial one (complete frames never stay buffered).
        if self._buf and self._looks_like_header(chunk):
            _LOGGER.debug("dropping stale partial frame: %s", self._buf.hex(" "))
            self._buf.clear()
        self._buf += chunk
        frames: list[Frame] = []
        while True:
            start = self._buf.find(HEAD_STD)
            if start < 0:
                self._buf.clear()
                break
            del self._buf[:start]
            exp = self._expected_len()
            if exp is None or len(self._buf) < exp:
                break
            frame = self._validate(bytes(self._buf[:exp]))
            if frame is None:
                del self._buf[:1]  # resync on the next head byte
                continue
            del self._buf[:exp]
            frames.append(frame)
        return frames

    @staticmethod
    def _looks_like_header(chunk: bytes | bytearray) -> bool:
        return (
            len(chunk) >= HEADER_LEN
            and chunk[0] == HEAD_STD
            and chunk[1] in RSP_VERSIONS
            and (chunk[3] & 0x7F) in (FUNC_READ, FUNC_WRITE)
        )

    def _expected_len(self) -> int | None:
        if len(self._buf) < HEADER_LEN:
            return None
        length = int.from_bytes(self._buf[6:8], "big")
        if length > MAX_PAYLOAD:
            return 1  # forces validation failure and resync
        return HEADER_LEN + length + TRAILER_LEN

    @staticmethod
    def _validate(raw: bytes) -> Frame | None:
        if len(raw) < HEADER_LEN + TRAILER_LEN or raw[-1] != TAIL:
            return None
        if raw[1] not in RSP_VERSIONS:
            _LOGGER.debug("unknown frame version 0x%02X: %s", raw[1], raw.hex(" "))
            return None
        register = int.from_bytes(raw[4:6], "big")
        crc_ok = crc_modbus(raw[:-3]) == int.from_bytes(raw[-3:-1], "big")
        # firmware 1.x sends 0x8D frames with a wrong, single-byte CRC
        if not crc_ok and (register != REG_STATUS or int.from_bytes(raw[-3:-1], "big") > 0xFF):
            _LOGGER.debug("CRC error: %s", raw.hex(" "))
            return None
        return Frame(
            version=raw[1],
            device_address=raw[2],
            function=raw[3],
            register=register,
            payload=raw[HEADER_LEN:-TRAILER_LEN],
            raw=raw,
            crc_ok=crc_ok,
        )


# --- alarm definitions (from the WattCycle app, bit 0 first) -------------------
# (key, German, English)
_A: Final = {
    # status 1 - protections
    "cell_overvoltage_protection": ("Zell-Überspannungsschutz", "Cell overvoltage protection"),
    "cell_undervoltage_protection": ("Zell-Unterspannungsschutz", "Cell undervoltage protection"),
    "pack_overvoltage_protection": ("Pack-Überspannungsschutz", "Pack overvoltage protection"),
    "pack_undervoltage_protection": ("Pack-Unterspannungsschutz", "Pack undervoltage protection"),
    "charge_overcurrent_protection": ("Überstromschutz Laden", "Charge overcurrent protection"),
    "discharge_overcurrent_protection": (
        "Überstromschutz Entladen",
        "Discharge overcurrent protection",
    ),
    "hardware_protection": ("Hardwareschutz", "Hardware protection"),
    "charge_voltage_high": ("Ladespannung zu hoch", "Charge voltage too high"),
    # status 2 - temperature protections
    "charge_high_temperature_protection": (
        "Übertemperaturschutz Laden",
        "Charge over-temperature protection",
    ),
    "discharge_high_temperature_protection": (
        "Übertemperaturschutz Entladen",
        "Discharge over-temperature protection",
    ),
    "charge_low_temperature_protection": (
        "Untertemperaturschutz Laden",
        "Charge under-temperature protection",
    ),
    "discharge_low_temperature_protection": (
        "Untertemperaturschutz Entladen",
        "Discharge under-temperature protection",
    ),
    "mos_high_temperature_protection": (
        "MOSFET-Übertemperaturschutz",
        "MOSFET over-temperature protection",
    ),
    "environment_high_temperature_protection": (
        "Umgebung zu heiß (Schutz)",
        "Ambient over-temperature protection",
    ),
    "environment_low_temperature_protection": (
        "Umgebung zu kalt (Schutz)",
        "Ambient under-temperature protection",
    ),
    # status 5 - faults
    "charge_mos_fault": ("Lade-MOSFET-Fehler", "Charge MOSFET fault"),
    "discharge_mos_fault": ("Entlade-MOSFET-Fehler", "Discharge MOSFET fault"),
    "temperature_fault": ("Temperaturfehler", "Temperature fault"),
    "cell_fault": ("Zellfehler", "Cell fault"),
    # warning 1
    "cell_overvoltage_warning": ("Warnung Zell-Überspannung", "Cell overvoltage warning"),
    "cell_undervoltage_warning": ("Warnung Zell-Unterspannung", "Cell undervoltage warning"),
    "pack_overvoltage_warning": ("Warnung Pack-Überspannung", "Pack overvoltage warning"),
    "pack_undervoltage_warning": ("Warnung Pack-Unterspannung", "Pack undervoltage warning"),
    "charge_overcurrent_warning": ("Warnung Überstrom Laden", "Charge overcurrent warning"),
    "discharge_overcurrent_warning": (
        "Warnung Überstrom Entladen",
        "Discharge overcurrent warning",
    ),
    "hardware_protection_warning": ("Warnung Hardwareschutz", "Hardware protection warning"),
    "charge_voltage_high_warning": ("Warnung Ladespannung hoch", "Charge voltage high warning"),
    # warning 2
    "charge_high_temperature_warning": (
        "Warnung Übertemperatur Laden",
        "Charge over-temperature warning",
    ),
    "discharge_high_temperature_warning": (
        "Warnung Übertemperatur Entladen",
        "Discharge over-temperature warning",
    ),
    "charge_low_temperature_warning": (
        "Warnung Untertemperatur Laden",
        "Charge under-temperature warning",
    ),
    "discharge_low_temperature_warning": (
        "Warnung Untertemperatur Entladen",
        "Discharge under-temperature warning",
    ),
    "environment_high_temperature_warning": (
        "Warnung Umgebung zu heiß",
        "Ambient over-temperature warning",
    ),
    "environment_low_temperature_warning": (
        "Warnung Umgebung zu kalt",
        "Ambient under-temperature warning",
    ),
    "mos_high_temperature_warning": ("Warnung MOSFET zu heiß", "MOSFET over-temperature warning"),
}
ALARM_TEXTS: Final = {k: {"de": de, "en": en} for k, (de, en) in _A.items()}
NO_ALARM_TEXT: Final = {"de": "Keine", "en": "None"}

ALARM_BITS: Final[dict[str, tuple[str | None, ...]]] = {
    "status_1": (
        "cell_overvoltage_protection",
        "cell_undervoltage_protection",
        "pack_overvoltage_protection",
        "pack_undervoltage_protection",
        "charge_overcurrent_protection",
        "discharge_overcurrent_protection",
        "hardware_protection",
        "charge_voltage_high",
    ),
    "status_2": (
        "charge_high_temperature_protection",
        "discharge_high_temperature_protection",
        "charge_low_temperature_protection",
        "discharge_low_temperature_protection",
        "mos_high_temperature_protection",
        "environment_high_temperature_protection",
        "environment_low_temperature_protection",
    ),
    "status_5": (
        "charge_mos_fault",
        "discharge_mos_fault",
        "temperature_fault",
        None,
        "cell_fault",
    ),
    "warning_1": (
        "cell_overvoltage_warning",
        "cell_undervoltage_warning",
        "pack_overvoltage_warning",
        "pack_undervoltage_warning",
        "charge_overcurrent_warning",
        "discharge_overcurrent_warning",
        "hardware_protection_warning",
        "charge_voltage_high_warning",
    ),
    # note: bits 4..6 are ordered differently than in status 2
    "warning_2": (
        "charge_high_temperature_warning",
        "discharge_high_temperature_warning",
        "charge_low_temperature_warning",
        "discharge_low_temperature_warning",
        "environment_high_temperature_warning",
        "environment_low_temperature_warning",
        "mos_high_temperature_warning",
    ),
}


def alarm_text(key: str, language: str = "en") -> str:
    """Human readable alarm name (German or English)."""
    lang = "de" if language.lower().startswith("de") else "en"
    return ALARM_TEXTS.get(key, {}).get(lang, key)


# state bytes for cells, temperature sensors, currents and pack voltage
STATE_NAMES: Final = {
    0x00: "normal",
    0x01: "low_warning",
    0x02: "high_warning",
    0xF0: "low_protection",
    0xF1: "high_protection",
}

BATTERY_MODES: Final = {0: "standby", 1: "charging", 2: "discharging"}


def state_name(value: int | None) -> str | None:
    """Map a state byte to its name."""
    if value is None:
        return None
    return STATE_NAMES.get(value, f"unknown_0x{value:02X}")


def _bits(value: int | None, names: tuple[str | None, ...]) -> list[str]:
    if not value:
        return []
    return [n for i, n in enumerate(names) if n and value >> i & 1]


# --- decoded data -------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Response to 0x92."""

    sw_version: str
    manufacturer: str
    serial_number: str
    hw_version: str = ""
    hw_info: str = ""
    sampling_resistor: int | None = None


@dataclass(frozen=True, slots=True)
class CellData:
    """Response to 0x8C (cells, temperatures, pack values)."""

    cell_voltages: tuple[float, ...]
    temp_mos: float | None
    temp_pcb: float | None
    temp_cells: tuple[float, ...]
    current: float
    voltage: float
    remaining_capacity: float
    full_capacity: float
    cycles: int
    design_capacity: float
    soc: int
    soh: int | None = None
    accumulated_capacity: float | None = None
    remaining_time: int | None = None  # minutes, as reported by BMS
    time_to_sleep: int | None = None  # minutes
    protection_count: int | None = None
    emergency_time: int | None = None  # minutes
    balance_current: float | None = None
    balance_samples: tuple[float, ...] = ()
    frame_layout: tuple[int, ...] = ()  # total cells, total temps, cell idx, temp idx

    @property
    def cell_count(self) -> int:
        """Number of cells."""
        return len(self.cell_voltages)


@dataclass(frozen=True, slots=True)
class StatusData:
    """Response to 0x8D (states, protections, MOSFETs, balancing)."""

    cell_states: tuple[int, ...]
    temp_states: tuple[int, ...]
    charge_current_state: int | None
    voltage_state: int | None
    discharge_current_state: int | None
    battery_mode: int | None
    status: tuple[int, ...]  # status 1..7
    warning1: int | None
    warning2: int | None
    balancing: tuple[bool, ...] | None
    extra: bytes = b""
    crc_ok: bool = True

    def _status(self, idx: int) -> int | None:
        return self.status[idx] if len(self.status) > idx else None

    @property
    def problem_code(self) -> int:
        """Status 1 (high) and status 2 (low) combined."""
        return ((self._status(0) or 0) << 8) | (self._status(1) or 0)

    @property
    def charge_mosfet(self) -> bool | None:
        """Charge switch / MOSFET on (status 3, bit 1)."""
        s3 = self._status(2)
        return bool(s3 & 0x02) if s3 is not None else None

    @property
    def discharge_mosfet(self) -> bool | None:
        """Discharge switch / MOSFET on (status 3, bit 2)."""
        s3 = self._status(2)
        return bool(s3 & 0x04) if s3 is not None else None

    @property
    def protections(self) -> list[str]:
        """Active protections (status 1 and 2)."""
        return _bits(self._status(0), ALARM_BITS["status_1"]) + _bits(
            self._status(1), ALARM_BITS["status_2"]
        )

    @property
    def faults(self) -> list[str]:
        """Active hardware faults (status 5)."""
        return _bits(self._status(4), ALARM_BITS["status_5"])

    @property
    def warnings(self) -> list[str]:
        """Active pre-warnings (warning 1 and 2)."""
        return _bits(self.warning1, ALARM_BITS["warning_1"]) + _bits(
            self.warning2, ALARM_BITS["warning_2"]
        )

    @property
    def alarms(self) -> list[str]:
        """Everything active, in the order the app lists it."""
        return self.protections + self.faults + self.warnings

    @property
    def alarm_active(self) -> bool:
        """Anything active (protection, fault or warning)."""
        return bool(self.alarms)

    @property
    def problem_active(self) -> bool:
        """A protection or fault is active."""
        return bool(self.protections or self.faults)

    @property
    def warning_active(self) -> bool:
        """A pre-warning is active."""
        return bool(self.warnings)


@dataclass(slots=True)
class WattcycleData:
    """Everything read in one poll."""

    cells: CellData
    status: StatusData
    info: DeviceInfo | None = None
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def power(self) -> float:
        """Pack power in W (positive: charging)."""
        return round(self.cells.voltage * self.cells.current, 2)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for diagnostics."""
        data = asdict(self)
        data["status"]["extra"] = self.status.extra.hex(" ")
        data["status"]["problem_code"] = self.status.problem_code
        data["status"]["charge_mosfet"] = self.status.charge_mosfet
        data["status"]["discharge_mosfet"] = self.status.discharge_mosfet
        data["status"]["protections"] = self.status.protections
        data["status"]["faults"] = self.status.faults
        data["status"]["warnings"] = self.status.warnings
        data["power"] = self.power
        return data


# --- payload decoders ---------------------------------------------------------
def parse_device_info(payload: bytes) -> DeviceInfo:
    """Decode 0x92 payload (60 bytes, or 112 bytes on newer firmware)."""
    long_form = len(payload) >= 112
    return DeviceInfo(
        sw_version=_b2str(payload[0:20]),
        manufacturer=_b2str(payload[20:40]),
        serial_number=_b2str(payload[40:60]),
        hw_version=_b2str(payload[60:80]) if long_form else "",
        hw_info=_b2str(payload[80:110]) if long_form else "",
        sampling_resistor=_u16(payload, 110) if long_form else None,
    )


_V1_FW: Final = re.compile(r"(^|[^0-9])1\.\d+$")


def capacity_scale_for_firmware(sw_version: str | None) -> float:
    """Return the capacity scaling factor for a firmware string.

    Recorded frames of firmware 1.x carry capacities in Ah, older firmware in 0.1 Ah.
    (The vendor app always divides by 10, hence the override option.)
    """
    if sw_version and _V1_FW.search(sw_version.strip()):
        return 1.0
    return 0.1


def parse_cell_data(payload: bytes, capacity_scale: float = 0.1) -> CellData:
    """Decode 0x8C payload."""
    if not payload:
        raise ValueError("empty 0x8C payload")
    n = payload[0]
    t_pos = 1 + 2 * n
    if len(payload) <= t_pos:
        raise ValueError("0x8C payload too short for cell count")
    t = payload[t_pos]
    base = t_pos + 1 + 2 * t  # current field
    if len(payload) < base + 14:
        raise ValueError(f"0x8C payload too short ({len(payload)} < {base + 14})")

    cells = tuple(int.from_bytes(payload[1 + 2 * i : 3 + 2 * i], "big") / 1000 for i in range(n))
    temps = [
        round(
            (int.from_bytes(payload[t_pos + 1 + 2 * j : t_pos + 3 + 2 * j], "big") - TEMP_OFFSET)
            / 10,
            1,
        )
        for j in range(t)
    ]

    def cap(pos: int) -> float:
        return round((_u16(payload, base + pos) or 0) * capacity_scale, 1)

    k = (n + 7) // 8
    acc = _u32(payload, base + 16)
    bal = _u16(payload, base + 30)
    layout_pos = base + 32 + 2 * k
    return CellData(
        cell_voltages=cells,
        temp_mos=temps[0] if t > 0 else None,
        temp_pcb=temps[1] if t > 1 else None,
        temp_cells=tuple(temps[2:]),
        current=decode_current(_u16(payload, base) or 0),
        voltage=(_u16(payload, base + 2) or 0) / 100,
        remaining_capacity=cap(4),
        full_capacity=cap(6),
        cycles=_u16(payload, base + 8) or 0,
        design_capacity=cap(10),
        soc=_u16(payload, base + 12) or 0,
        soh=_u16(payload, base + 14),
        accumulated_capacity=round(acc * capacity_scale, 1) if acc is not None else None,
        remaining_time=_u32(payload, base + 20, signed=True),
        time_to_sleep=_u16(payload, base + 24),
        protection_count=_u16(payload, base + 26),
        emergency_time=_u16(payload, base + 28),
        balance_current=decode_current(bal) if bal is not None else None,
        balance_samples=tuple(
            v / 1000 for i in range(k) if (v := _u16(payload, base + 32 + 2 * i)) is not None
        ),
        frame_layout=tuple(payload[layout_pos : layout_pos + 4])
        if len(payload) >= layout_pos + 4
        else (),
    )


def parse_status(payload: bytes, crc_ok: bool = True) -> StatusData:
    """Decode 0x8D payload."""
    if not payload:
        raise ValueError("empty 0x8D payload")
    n = payload[0]
    if len(payload) <= n + 1:
        raise ValueError("0x8D payload too short for cell count")
    t = payload[n + 1]
    k = n + t
    if len(payload) < k + 2 + 7:
        raise ValueError("0x8D payload too short")
    bm_len = (n + 7) // 8
    bm_pos = k + 15
    balancing: tuple[bool, ...] | None = None
    if len(payload) >= bm_pos + bm_len:
        mask = int.from_bytes(payload[bm_pos : bm_pos + bm_len], "little")
        balancing = tuple(bool(mask >> i & 1) for i in range(n))
    return StatusData(
        cell_states=tuple(payload[1 : n + 1]),
        temp_states=tuple(payload[n + 2 : n + 2 + t]),
        charge_current_state=_u8(payload, k + 2),
        voltage_state=_u8(payload, k + 3),
        discharge_current_state=_u8(payload, k + 4),
        battery_mode=_u8(payload, k + 5),
        status=tuple(payload[k + 6 : k + 13]),
        warning1=_u8(payload, k + 13),
        warning2=_u8(payload, k + 14),
        balancing=balancing,
        extra=bytes(payload[bm_pos + bm_len :]),
        crc_ok=crc_ok,
    )
