# Redfish BMC

The Redfish resource provides DMTF Redfish BMC compatibility for PiKVM.

!!! warning
    Redfish endpoints do **not** use the standard PiKVM response format (`{"ok": true, "result": ...}`). They return plain JSON bodies.

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
