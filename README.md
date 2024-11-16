# pyfelicita
## Felicita integration package for python

Example code:
```
import asyncio
from pyfelicita import Felicita

async def main():
    addresses = await Felicita.find_felicita_devices()
    
    if not addresses:
        print("No devices found")
        return
        
    address = addresses[0]
    
    client = await Felicita.create(address)
        
    if not client:
        print("Failed to connect to the scale")
        return
    
    try:
        await client.set_weight_and_timer_mode()
        await asyncio.sleep(0.1)
        await client.tare()
        await asyncio.sleep(0.1)
        await client.reset_timer()
        await asyncio.sleep(0.1)
        await client.start_timer()
        
        while client.is_connected():
            await asyncio.sleep(0.1)
            print(
                f"Weight: {client.current_weight} {client.current_scale_unit}, "
                f"Battery: {client.current_battery_level:.2f}%"
            )
            
            if(client.is_timer_running):
                print(f"Time elapsed: {client.timer_time_elapsed}")
                
            if(client.current_weight > 69):
                await client.stop_timer()
                break
                    
    finally:
        if client.is_connected():
            await client.disconnect()
        else:
            print("Scale stopped responding")

asyncio.run(main())
```