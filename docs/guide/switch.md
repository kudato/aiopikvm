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
