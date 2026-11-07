"""Bluetooth client for WattCycle batteries (TDT smart BMS)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from typing import Final

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    close_stale_connections,
    establish_connection,
)

from .protocol import (
    CHAR_NOTIFY_UUID,
    CHAR_UNLOCK_UUID,
    CHAR_WRITE_UUID,
    CMD_HEADS,
    FRAME_VERSION,
    FUNC_READ,
    FUNC_WRITE,
    REG_CELL_DATA,
    REG_CHARGE_SWITCH,
    REG_DEVICE_INFO,
    REG_DISCHARGE_SWITCH,
    REG_STATUS,
    SERVICE_UUID,
    UNLOCK_ACK,
    UNLOCK_KEY,
    DeviceInfo,
    Frame,
    FrameAssembler,
    StatusData,
    WattcycleData,
    build_command,
    capacity_scale_for_firmware,
    parse_cell_data,
    parse_device_info,
    parse_status,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = 2.0  # seconds per attempt
REQUEST_RETRIES: Final = 2  # attempts per command head
SWITCH_SETTLE_TIME: Final = 0.5  # seconds between status reads after a switch command
SWITCH_CONFIRM_TIMEOUT: Final = 6.0  # seconds until a switch command counts as not effective


class WattcycleError(Exception):
    """Communication with the battery failed."""


class WattcycleNotSupportedError(WattcycleError):
    """Device does not speak the TDT protocol (e.g. JK or JBD BMS)."""


class WattcycleCommandRejectedError(WattcycleError):
    """The BMS rejected a command (function 0x83/0x86)."""


class WattcycleSwitchError(WattcycleError):
    """The BMS accepted a switch command but the MOSFET did not change."""


class WattcycleClient:
    """Connection to one battery."""

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        capacity_scale: float | None = None,
        keep_alive: bool = True,
    ) -> None:
        """Initialize client; `capacity_scale=None` means detect from firmware."""
        self._ble_device = ble_device
        self._capacity_scale_override = capacity_scale
        self._keep_alive = keep_alive
        self._client: BleakClientWithServiceCache | None = None
        self._assembler = FrameAssembler()
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        self._lock = asyncio.Lock()
        self._head: int | None = None  # detected command head
        self._info: DeviceInfo | None = None
        self._last_raw: dict[str, str] = {}
        self._handshake_result: str = "not attempted"
        self._write_version: int | None = None  # framing version accepted for writes

    # --- public -------------------------------------------------------------
    @property
    def address(self) -> str:
        """Bluetooth address."""
        return self._ble_device.address

    @property
    def device_info(self) -> DeviceInfo | None:
        """Last device info (0x92)."""
        return self._info

    @property
    def capacity_scale(self) -> float:
        """Active capacity scaling factor."""
        if self._capacity_scale_override is not None:
            return self._capacity_scale_override
        return capacity_scale_for_firmware(self._info.sw_version if self._info else None)

    @property
    def is_connected(self) -> bool:
        """True if a BLE connection is up."""
        return bool(self._client and self._client.is_connected)

    def diagnostics(self) -> dict[str, object]:
        """Connection details for diagnostics."""
        return {
            "connected": self.is_connected,
            "command_head": f"0x{self._head:02X}" if self._head is not None else None,
            "handshake": self._handshake_result,
            "capacity_scale": self.capacity_scale,
            "capacity_scale_override": self._capacity_scale_override,
            "write_version": self._write_version,
            "last_raw_frames": dict(self._last_raw),
        }

    @staticmethod
    def replace_status(data: WattcycleData, status: StatusData) -> WattcycleData:
        """Copy of `data` with a fresh status (after a switch command)."""
        return replace(data, status=status)

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update the BLEDevice (e.g. a different adapter or proxy)."""
        self._ble_device = ble_device

    async def async_update(self) -> WattcycleData:
        """Read all data from the battery."""
        async with self._lock:
            try:
                await self._ensure_connected()
                if self._info is None:
                    try:
                        frame = await self._request(REG_DEVICE_INFO)
                        self._info = parse_device_info(frame.payload)
                        _LOGGER.debug("%s: device info %s", self.address, self._info)
                    except (TimeoutError, ValueError, WattcycleCommandRejectedError) as err:
                        _LOGGER.debug("%s: no device info (%s)", self.address, err)
                cell_frame = await self._request(REG_CELL_DATA)
                status_frame = await self._request(REG_STATUS)
                data = WattcycleData(
                    cells=parse_cell_data(cell_frame.payload, self.capacity_scale),
                    status=parse_status(status_frame.payload, status_frame.crc_ok),
                    info=self._info,
                    raw=dict(self._last_raw),
                )
            except WattcycleError:
                await self._disconnect()
                raise
            except (BleakError, TimeoutError, EOFError, ValueError) as err:
                await self._disconnect(reset=isinstance(err, BleakError))
                raise WattcycleError(f"{type(err).__name__}: {err}") from err
            if not self._keep_alive:
                await self._disconnect()
            return data

    async def async_set_switch(self, register: int, on: bool) -> StatusData:
        """Switch charging (0x82) or discharging (0x83) and confirm via 0x8D."""
        if register not in (REG_CHARGE_SWITCH, REG_DISCHARGE_SWITCH):
            raise ValueError(f"not a switch register: 0x{register:02X}")
        async with self._lock:
            try:
                await self._ensure_connected()
                _LOGGER.info(
                    "%s: switching %s %s",
                    self.address,
                    "charge" if register == REG_CHARGE_SWITCH else "discharge",
                    "on" if on else "off",
                )
                await self._write(register, b"\x01" if on else b"\x00")
                status = await self._await_switch_state(register, on)
            except WattcycleCommandRejectedError:
                raise
            except WattcycleError:
                await self._disconnect()
                raise
            except (BleakError, TimeoutError, EOFError, ValueError) as err:
                await self._disconnect(reset=isinstance(err, BleakError))
                raise WattcycleError(f"{type(err).__name__}: {err}") from err
            finally:
                if not self._keep_alive:
                    await self._disconnect()
        if self._switch_state(status, register) is not on:
            raise WattcycleSwitchError(
                f"switch 0x{register:02X} still {'on' if not on else 'off'} "
                f"{SWITCH_CONFIRM_TIMEOUT:.0f} s after command "
                f"(status 3 = 0x{status.status[2] if len(status.status) > 2 else 0:02X})"
            )
        return status

    @staticmethod
    def _switch_state(status: StatusData, register: int) -> bool | None:
        return status.charge_mosfet if register == REG_CHARGE_SWITCH else status.discharge_mosfet

    async def _await_switch_state(self, register: int, on: bool) -> StatusData:
        """Poll 0x8D until the MOSFET follows; the BMS updates status 3 with a delay."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SWITCH_CONFIRM_TIMEOUT
        while True:
            await asyncio.sleep(SWITCH_SETTLE_TIME)
            frame = await self._request(REG_STATUS)
            status = parse_status(frame.payload, frame.crc_ok)
            state = self._switch_state(status, register)
            _LOGGER.debug(
                "%s: after switch 0x%02X -> %s: status 3 = %s",
                self.address,
                register,
                on,
                f"0x{status.status[2]:02X}" if len(status.status) > 2 else "?",
            )
            if state is on or loop.time() >= deadline:
                return status

    async def disconnect(self) -> None:
        """Close the connection."""
        async with self._lock:
            await self._disconnect()

    # --- connection -----------------------------------------------------------
    async def _ensure_connected(self) -> None:
        if self.is_connected:
            return
        _LOGGER.debug("%s: connecting", self.address)
        self._assembler.reset()
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._ble_device.name or self.address,
            disconnected_callback=self._on_disconnect,
            ble_device_callback=lambda: self._ble_device,
        )
        services = self._client.services
        if services.get_characteristic(CHAR_WRITE_UUID) is None or (
            hasattr(services, "get_service") and services.get_service(SERVICE_UUID) is None
        ):
            await self._disconnect()
            raise WattcycleNotSupportedError(
                f"{self.address} has no FFF0 service: not a TDT BMS (JK/JBD?)"
            )
        # Unlock first: without it the BMS ignores requests and drops the link after ~4 s.
        await self._handshake()
        await self._client.start_notify(CHAR_NOTIFY_UUID, self._on_notify)
        _LOGGER.debug("%s: connected", self.address)

    async def _handshake(self) -> None:
        assert self._client
        if self._client.services.get_characteristic(CHAR_UNLOCK_UUID) is None:
            self._handshake_result = "skipped (no FFFA characteristic)"
            return
        await self._client.write_gatt_char(CHAR_UNLOCK_UUID, UNLOCK_KEY, response=True)
        ack = bytes(await self._client.read_gatt_char(CHAR_UNLOCK_UUID))
        if int.from_bytes(ack or b"\x00", "big") == UNLOCK_ACK:
            self._handshake_result = "ok"
        else:
            self._handshake_result = f"unexpected ack {ack.hex()}"
            _LOGGER.warning("%s: unexpected unlock response %s", self.address, ack.hex())

    def _on_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: disconnected", self.address)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(EOFError("disconnected"))

    async def _disconnect(self, reset: bool = False) -> None:
        client, self._client = self._client, None
        if client:
            try:
                await client.disconnect()
            except (BleakError, TimeoutError, EOFError) as err:
                _LOGGER.debug("%s: disconnect failed (%s)", self.address, err)
        if reset:
            self._head = None
            await close_stale_connections(self._ble_device)

    # --- request/response -------------------------------------------------------
    def _on_notify(self, _char: BleakGATTCharacteristic | int, data: bytearray) -> None:
        _LOGGER.debug("%s: RX %s", self.address, data.hex(" "))
        for frame in self._assembler.feed(data):
            self._last_raw[f"0x{frame.register:02X}"] = frame.raw.hex(" ")
            fut = self._pending.get(frame.register)
            if fut is None or fut.done():
                _LOGGER.debug("%s: unsolicited frame 0x%02X", self.address, frame.register)
                continue
            if frame.is_error:
                fut.set_exception(
                    WattcycleCommandRejectedError(
                        f"BMS rejected 0x{frame.register:02X} (function 0x{frame.function:02X})"
                    )
                )
            else:
                fut.set_result(frame)

    async def _write(self, register: int, data: bytes) -> Frame:
        """Write a register; fall back to version-1 framing if version 0 is ignored."""
        versions = (
            (self._write_version,) if self._write_version is not None else (FRAME_VERSION, 0x01)
        )
        for version in versions:
            try:
                frame = await self._request(register, data, FUNC_WRITE, version)
            except TimeoutError:
                _LOGGER.debug("%s: no answer to write with version %d", self.address, version)
                continue
            self._write_version = version
            return frame
        raise TimeoutError(f"no response to write 0x{register:02X}")

    async def _request(
        self,
        register: int,
        data: bytes = b"",
        function: int = FUNC_READ,
        version: int = FRAME_VERSION,
    ) -> Frame:
        assert self._client
        char = self._client.services.get_characteristic(CHAR_WRITE_UUID)
        if char is None:
            raise BleakError("write characteristic FFF2 not found")
        with_response = "write" in char.properties
        # head is known after the first poll; writes are idempotent if probed
        heads = (self._head,) if self._head is not None else CMD_HEADS
        loop = asyncio.get_running_loop()
        for head in heads:
            frame_out = build_command(register, data, head, function, version)
            for attempt in range(REQUEST_RETRIES):
                fut: asyncio.Future[Frame] = loop.create_future()
                self._pending[register] = fut
                _LOGGER.debug("%s: TX %s", self.address, frame_out.hex(" "))
                try:
                    await self._client.write_gatt_char(char, frame_out, response=with_response)
                    frame = await asyncio.wait_for(fut, REQUEST_TIMEOUT * (attempt + 1))
                except TimeoutError:
                    continue
                finally:
                    self._pending.pop(register, None)
                if self._head is None:
                    _LOGGER.debug("%s: command head 0x%02X", self.address, head)
                    self._head = head
                return frame
        raise TimeoutError(f"no response to 0x{register:02X}")
