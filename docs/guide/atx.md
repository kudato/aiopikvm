# ATX Power Control

The ATX resource controls host power: power on/off, reset, and reading LED indicators.

## Get state

```python
state = await kvm.atx.get_state()
print(f"Enabled: {state.enabled}")
print(f"Busy: {state.busy}")
print(f"Power LED: {state.leds.power}")
print(f"HDD LED: {state.leds.hdd}")
print(f"Power action running: {state.acts.power}")
```

The returned `ATXState` contains:

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | `bool` | Whether ATX is enabled |
| `busy` | `bool` | Whether any operation is in progress |
| `acts.power` | `bool` | A power action is running |
| `acts.reset` | `bool` | A reset action is running |
| `leds.power` | `bool` | Power LED state |
| `leds.hdd` | `bool` | HDD activity LED state |

kvmd guards the power and reset lines separately — `busy` is the two of them
combined, while `acts` says which one is occupied.

## When a call fails

```python
from aiopikvm import APIError, BusyError

try:
    await kvm.atx.click_power()
except BusyError:
    print("Another ATX action is still running")
except APIError as exc:
    if exc.error == "AtxDisabledError":
        print("The ATX plugin is disabled on this device")
```

`BusyError` (HTTP 409) comes back whether or not you wait. A disabled plugin
answers HTTP 400 on every action, and `state.enabled` tells you up front.

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

All ATX operations accept `wait`, which defaults to `False` — the same default
kvmd itself uses. The call returns as soon as kvmd has accepted the action, and
anything that fails while it runs goes to kvmd's log rather than to the caller:

```python
# Return immediately (default)
await kvm.atx.power_on()

# Block until the action has finished
await kvm.atx.power_on(wait=True)
```

Waiting holds the HTTP request open for the whole action. A long power click
alone holds the button for 5.5 seconds of the 10-second client default, so give
it room:

```python
await kvm.atx.click_power_long(wait=True, timeout=30.0)
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
