# Redfish BMC

The Redfish resource provides DMTF Redfish BMC compatibility for PiKVM.

!!! warning
    Redfish endpoints do **not** use the standard PiKVM response format (`{"ok": true, "result": ...}`). They return plain JSON bodies.

## Service root

```python
root = await kvm.redfish.get_root()
print(root["RedfishVersion"])
```

## Systems

```python
# List all systems
systems = await kvm.redfish.get_systems()
print(f"Count: {systems['Members@odata.count']}")

# Get system details
system = await kvm.redfish.get_system()
print(f"Power: {system['PowerState']}")

# Get a specific system by ID
system = await kvm.redfish.get_system(1)
```

## Update system

```python
result = await kvm.redfish.update_system(IndicatorLED="Lit")
print(result["IndicatorLED"])
```

## Reset

Send a Redfish `ComputerSystem.Reset` action:

```python
result = await kvm.redfish.reset()
print(result)
```

### Reset types

```python
# Force restart (default)
await kvm.redfish.reset("ForceRestart")

# Force off
await kvm.redfish.reset("ForceOff")

# Force on
await kvm.redfish.reset("ForceOn")

# Graceful shutdown
await kvm.redfish.reset("GracefulShutdown")

# Graceful restart
await kvm.redfish.reset("GracefulRestart")
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        result = await kvm.redfish.reset("GracefulRestart")
        print(f"Redfish response: {result}")

asyncio.run(main())
```
