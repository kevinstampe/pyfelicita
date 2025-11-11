import asyncio
import random
from datetime import datetime, timedelta
from time import gmtime, strftime
from contextlib import suppress
from typing import Optional, Callable

from bleak import BleakClient, BleakScanner, BleakError
from pyfelicita.constants import (
    DATA_CHARACTERISTIC_UUID,
    MIN_BATTERY_LEVEL,
    MAX_BATTERY_LEVEL,
    CMD_WEIGHT_ONLY_MODE,
    CMD_WEIGHT_AND_TIMER_MODE,
    CMD_START_TIMER_BY_FLOW_MODE,
    CMD_START_TIMER_BY_FLOW_AUTOTARE_MODE,
    CMD_START_TIMER_BY_TARE_AUTOTARE_MODE,
    CMD_TOGGLE_BEEP,
    CMD_RESET_TIMER,
    CMD_SET_MAX_WEIGHT_2KG,
    CMD_START_TIMER,
    CMD_STOP_TIMER,
    CMD_TARE,
    CMD_TOGGLE_UNIT,
    CMD_SET_MAX_WEIGHT_1KG,
    SCALE_START_NAMES,
)

class Felicita:
    """
    API-compatible with your original class, but:
      - resilient auto-reconnect loop
      - single write queue with retries + pacing
      - safe notify handler
      - proper disconnect callback wiring
    """

    def __init__(self, address: str, disconnect_callback: Optional[Callable] = None, timeout: float = 10.0):
        self.address = address
        self._timeout = timeout
        self._disconnect_cb = disconnect_callback

        self.BLEClient: Optional[BleakClient] = None
        self._connected_evt = asyncio.Event()
        self._stop_evt = asyncio.Event()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._writer_task: Optional[asyncio.Task] = None
        self._write_q: "asyncio.Queue[bytes]" = asyncio.Queue()

        # public-facing state
        self.current_weight = 0.0
        self.current_battery_level = 0.0
        self.current_scale_unit = ""
        self.last_battery_level_raw = 0
        self.is_timer_running = False
        self.timer_start_time: Optional[datetime] = None
        self.timer_time_elapsed = "00:00"
        self.last_update = datetime.min
        self.malformed_count = 0

    # ---------- factory/lifecycle ----------

    @classmethod
    async def create(cls, address, disconnect_callback=None):
        self = cls(address, disconnect_callback)
        await self._start()
        # Wait briefly for the first connection
        try:
            await asyncio.wait_for(self._connected_evt.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            await self.disconnect()
            return None
        return self

    async def _start(self):
        # kick off reconnect loop
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._run_with_reconnect())

    async def disconnect(self):
        self._stop_evt.set()

        if self._writer_task:
            self._writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._writer_task

        if self.BLEClient and self.BLEClient.is_connected:
            with suppress(Exception):
                await self.BLEClient.disconnect()

        if self._reconnect_task:
            self._reconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reconnect_task

    def is_connected(self):
        """Combines Bleak connection state with freshness of last update."""
        fresh = (datetime.now() - self.last_update) < timedelta(seconds=2)
        return bool(self.BLEClient and self.BLEClient.is_connected) and fresh

    # ---------- reconnect & post-connect ----------

    async def _run_with_reconnect(self):
        backoff = 1.0
        while not self._stop_evt.is_set():
            try:
                client = BleakClient(self.address, timeout=self._timeout, disconnected_callback=self._on_disconnected)
                await client.connect()
                self.BLEClient = client

                # start notify + writer
                await self._post_connect()
                self._connected_evt.set()
                backoff = 1.0  # reset on success

                # park until disconnected/stop
                while not self._stop_evt.is_set() and client.is_connected:
                    await asyncio.sleep(0.2)

            except Exception:
                # transient failure: retry with jittered backoff
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 15.0)
            finally:
                # ensure client object is torn down between attempts
                if self.BLEClient:
                    with suppress(Exception):
                        await self.BLEClient.disconnect()
                self.BLEClient = None

    async def _post_connect(self):
        # Cache services (best effort)
        with suppress(Exception):
            _ = self.BLEClient.services

        # Start notifications (non-blocking handler)
        await self.BLEClient.start_notify(DATA_CHARACTERISTIC_UUID, self._notification_handler)

        # Start writer loop if not already running
        if not self._writer_task or self._writer_task.done():
            self._writer_task = asyncio.create_task(self._writer_loop())

    def _on_disconnected(self, _client):
        # forward to user callback (if any)
        if self._disconnect_cb:
            try:
                self._disconnect_cb(_client)
            except Exception:
                pass
        # reconnect loop will spin again

    # ---------- notifications & parsing ----------

    def _notification_handler(self, _sender: int, data: bytearray):
        # Make parsing async but non-blocking for the BLE thread
        asyncio.create_task(self._parse_status_update(bytearray(data)))

    async def _parse_status_update(self, felicita_raw_status: bytearray):
        if len(felicita_raw_status) != 18:
            self.malformed_count += 1
            if self.malformed_count > 5:
                print("Scale returned malformed data")
            return

        self.malformed_count = 0

        # weight: bytes 3..8 ASCII digits
        weight_bytes = felicita_raw_status[3:9]
        weight = "".join([str(b - 48) for b in weight_bytes])  # keep your original approach
        scale_unit = bytes(felicita_raw_status[9:11]).decode("utf-8", errors="ignore")
        battery_level = felicita_raw_status[15]

        # simple battery smoothing
        if abs(self.last_battery_level_raw - battery_level) < 2:
            battery_level = self.last_battery_level_raw
        else:
            self.last_battery_level_raw = battery_level

        battery_percentage = ((battery_level - MIN_BATTERY_LEVEL) / (MAX_BATTERY_LEVEL - MIN_BATTERY_LEVEL)) * 100
        battery_percentage = max(0, min(battery_percentage, 100))

        is_negative = felicita_raw_status[2] == 45 and weight != "000000"
        self.current_weight = (-int(weight) if is_negative else int(weight)) / 100.0
        self.current_battery_level = battery_percentage
        self.current_scale_unit = scale_unit.strip()

        # timer
        time_elapsed = (datetime.now() - self.timer_start_time).total_seconds() if self.is_timer_running and self.timer_start_time else 0
        self.timer_time_elapsed = strftime("%M:%S", gmtime(time_elapsed))

        self.last_update = datetime.now()

    # ---------- write queue with retries ----------

    async def _writer_loop(self):
        while not self._stop_evt.is_set():
            payload = await self._write_q.get()
            try:
                for attempt in range(3):
                    try:
                        if not (self.BLEClient and self.BLEClient.is_connected):
                            # wait until reconnected
                            await asyncio.sleep(0.2)
                            continue

                        await asyncio.wait_for(
                            self.BLEClient.write_gatt_char(DATA_CHARACTERISTIC_UUID, payload, response=True),
                            timeout=3.0,
                        )
                        # small pacing gap to avoid flooding stacks/firmware
                        await asyncio.sleep(0.06)
                        break
                    except (asyncio.TimeoutError, BleakError):
                        await asyncio.sleep(0.2 * (attempt + 1))
            finally:
                self._write_q.task_done()

    async def send_command(self, command: int):
        # enqueue instead of writing directly
        await self._write_q.put(bytearray([command]))

    # ---------- public commands (unchanged signatures) ----------

    async def set_weight_only_mode(self):
        await self.send_command(CMD_WEIGHT_ONLY_MODE)

    async def set_weight_and_timer_mode(self):
        await self.send_command(CMD_WEIGHT_AND_TIMER_MODE)

    async def set_start_timer_by_flow_mode(self):
        await self.send_command(CMD_START_TIMER_BY_FLOW_MODE)

    async def set_start_timer_by_flow_autotare_mode(self):
        await self.send_command(CMD_START_TIMER_BY_FLOW_AUTOTARE_MODE)

    async def set_start_timer_by_tare_autotare_mode(self):
        await self.send_command(CMD_START_TIMER_BY_TARE_AUTOTARE_MODE)

    async def toggle_beep(self):
        await self.send_command(CMD_TOGGLE_BEEP)

    async def reset_timer(self):
        await self.send_command(CMD_RESET_TIMER)

    async def set_max_weight_1kg(self):
        await self.send_command(CMD_SET_MAX_WEIGHT_1KG)

    async def set_max_weight_2kg(self):
        await self.send_command(CMD_SET_MAX_WEIGHT_2KG)

    async def start_timer(self):
        await self.send_command(CMD_START_TIMER)
        self.timer_start_time = datetime.now()
        self.is_timer_running = True

    async def stop_timer(self):
        await self.send_command(CMD_STOP_TIMER)
        self.is_timer_running = False

    async def tare(self):
        await self.send_command(CMD_TARE)

    async def toggle_unit(self):
        await self.send_command(CMD_TOGGLE_UNIT)

    # ---------- discovery ----------

    async def find_felicita_devices():
        """Returns a list of device addresses.
        NOTE: kept compatible with your current README usage.
        """
        await asyncio.sleep(1)  # keep behavior
        scanner = BleakScanner()
        devices = await scanner.discover(timeout=5.0)
        addresses = []
        seen = set()
        for d in devices:
            name = (d.name or "").strip()
            if name and any(name.startswith(prefix) for prefix in SCALE_START_NAMES):
                if d.address not in seen:
                    print(name, d.address)
                    seen.add(d.address)
                    addresses.append(d.address)
        return addresses
