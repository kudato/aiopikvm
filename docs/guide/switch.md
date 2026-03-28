# KVM Switch

The Switch resource manages multi-port KVM switching and EDID profiles.

## Get state

```python
state = await kvm.switch.get_state()
print(f"Active port: {state.active}")

for port_id, port in state.ports.items():
    print(f"Port {port_id}: {port.name}")
```

## Switch active port

```python
await kvm.switch.set_active("port1")
```

## EDID management

### List EDID profiles

```python
edids = await kvm.switch.get_edids()
for edid in edids:
    print(f"ID: {edid.id}, Description: {edid.description}")
```

### Create an EDID profile

```python
await kvm.switch.create_edid(
    "custom-1080p",
    data="00ffffffffffff...",  # hex-encoded EDID data
    description="Custom 1080p profile",
)
```

### Assign EDID to a port

```python
await kvm.switch.change_edid("port1", "custom-1080p")
```

### Remove an EDID profile

```python
await kvm.switch.remove_edid("custom-1080p")
```

## Quick port switching

```python
# Switch to previous port
await kvm.switch.set_active_prev()

# Switch to next port
await kvm.switch.set_active_next()
```

## Beacon indicators

```python
# Turn on beacon for port 3
await kvm.switch.set_beacon(True, port=3)

# Turn off all beacons
await kvm.switch.set_beacon(False)

# Set beacon colors (RRGGBB:brightness:interval)
await kvm.switch.set_colors("FFA500:BF:0028")
```

## Port configuration

```python
await kvm.switch.set_port_params(
    0,
    name="Server1",
    edid_id="custom-1080p",
    dummy=True,
    atx_click_power_delay=1.5,
)
```

## ATX power control per port

```python
# Power actions: "on", "off", "off_hard", "reset_hard"
await kvm.switch.atx_power(0, "on")

# Button clicks: "power", "power_long", "reset"
await kvm.switch.atx_click(0, "power")
```

## Reboot switch unit

```python
# Reboot unit 0
await kvm.switch.reset(0)

# Reboot into bootloader (reflashing mode)
await kvm.switch.reset(1, bootloader=True)
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        # List available ports
        state = await kvm.switch.get_state()
        print(f"Current: {state.active}")
        for port_id, port in state.ports.items():
            marker = " (active)" if port_id == state.active else ""
            print(f"  {port_id}: {port.name}{marker}")

        # Switch to another port
        await kvm.switch.set_active("port2")

asyncio.run(main())
```
