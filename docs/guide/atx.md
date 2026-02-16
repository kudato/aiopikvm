# ATX Power Control

The ATX resource controls host power: power on/off, reset, and reading LED indicators.

## Get state

```python
state = await kvm.atx.get_state()
print(f"Enabled: {state.enabled}")
print(f"Busy: {state.busy}")
print(f"Power LED: {state.leds.power}")
print(f"HDD LED: {state.leds.hdd}")
```

The returned `ATXState` contains:

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | `bool` | Whether ATX is enabled |
| `busy` | `bool` | Whether an operation is in progress |
| `leds.power` | `bool` | Power LED state |
| `leds.hdd` | `bool` | HDD activity LED state |

## Power on / off

```python
# Power on the host
await kvm.atx.power_on()

# Graceful power off
await kvm.atx.power_off()

# Force power off (like holding the power button)
await kvm.atx.power_off_hard()
```

## Button clicks

```python
# Short power button press
await kvm.atx.click_power()

# Long power button press
await kvm.atx.click_power_long()

# Reset button press
await kvm.atx.click_reset()
```

## Hard reset

```python
await kvm.atx.reset_hard()
```

## The `wait` parameter

All ATX operations accept a `wait` parameter (default `True`). When `True`, the method waits for the operation to complete before returning:

```python
# Don't wait for completion
await kvm.atx.power_on(wait=False)

# Wait for completion (default)
await kvm.atx.power_on(wait=True)
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        state = await kvm.atx.get_state()

        if not state.leds.power:
            print("Host is off, powering on...")
            await kvm.atx.power_on()
        else:
            print("Host is on, resetting...")
            await kvm.atx.click_reset()

asyncio.run(main())
```
