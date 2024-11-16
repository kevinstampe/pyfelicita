import asyncio
from bleak import BleakClient, BleakScanner
from datetime import datetime, timedelta
from time import gmtime, strftime
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
    SCALE_START_NAMES
)

class Felicita:
    def __init__(self, address):
        self.address = address
        self.BLEClient = BleakClient(address)
        self.current_weight = 0
        self.current_battery_level = 0
        self.current_scale_unit = ""
        self.last_battery_level_raw = 0
        self.is_timer_running = False
        self.timer_start_time = None
        self.timer_time_elapsed = 0
        self.last_update = None

    @classmethod
    async def create(cls, address):
        instance = cls(address)
        instance.BLEClient = BleakClient(address)
        try:
            await instance.BLEClient.connect()
            await instance.BLEClient.start_notify(DATA_CHARACTERISTIC_UUID, instance._notification_handler)
            print("Connected to the scale")
            return instance
        except Exception as e:
            print(f"Error: {e}")
            await instance.BLEClient.disconnect()
            return None
                
    async def disconnect(self):
        await self.BLEClient.disconnect()

    def is_connected(self):
        return datetime.now() - self.last_update < timedelta(seconds=5)
    
    async def _disconnect_callback(self):
        await self.BLEClient.stop_notify(DATA_CHARACTERISTIC_UUID)
        self.is_connected = False
        print("Disconnected from the scale")

    async def _parse_status_update(self, felicita_raw_status):
        if len(felicita_raw_status) != 18:
            print("felicita, Malformed data")
            return

        weight_bytes = felicita_raw_status[3:9]
        weight = "".join([str(b - 48) for b in weight_bytes])
        scale_unit = bytes(felicita_raw_status[9:11]).decode("utf-8")
        battery_level = felicita_raw_status[15]

        if abs(self.last_battery_level_raw - battery_level) < 2:
            battery_level = self.last_battery_level_raw
        else:
            self.last_battery_level_raw = battery_level

        battery_percentage = ((battery_level - MIN_BATTERY_LEVEL) / (MAX_BATTERY_LEVEL - MIN_BATTERY_LEVEL)) * 100
        battery_percentage = max(0, min(battery_percentage, 100))

        is_negative = felicita_raw_status[2] == 45 and weight != "000000"
        self.current_weight = -int(weight) / 100 if is_negative else int(weight) / 100
        self.current_battery_level = battery_percentage
        self.current_scale_unit = scale_unit.strip()
        
        time_elapsed = (datetime.now() - self.timer_start_time).total_seconds() if self.is_timer_running else 0
        
        self.timer_time_elapsed = strftime("%M:%S", gmtime(time_elapsed))
        
        self.last_update = datetime.now()
        

    async def _notification_handler(self, sender, data):
        await self._parse_status_update(bytearray(data))

    async def send_command(self, command):
        await self.BLEClient.write_gatt_char(DATA_CHARACTERISTIC_UUID, bytearray([command]))

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
        
    async def find_felicita_devices():
        addresses = []
        await asyncio.sleep(1)
        scanner = BleakScanner()
        devices = await scanner.discover(timeout=5.0)
        for d in devices:
            if d.name and any(d.name.startswith(name) for name in SCALE_START_NAMES):
                print(d.name, d.address)
                addresses.append(d.address)
        return addresses

