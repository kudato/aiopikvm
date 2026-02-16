# GPIO Channels

The GPIO resource reads and controls GPIO channels on PiKVM.

## Get state

```python
state = await kvm.gpio.get_state()

# Input channels
for name, input_ch in state.inputs.items():
    print(f"Input {name}: online={input_ch.online}, state={input_ch.state}")

# Output channels
for name, output_ch in state.outputs.items():
    print(f"Output {name}: online={output_ch.online}, state={output_ch.state}, busy={output_ch.busy}")
```

The returned `GPIOState` contains:

| Field | Type | Description |
|-------|------|-------------|
| `inputs` | `dict[str, GPIOInput]` | Input channel states |
| `outputs` | `dict[str, GPIOChannel]` | Output channel states |

## Switch output

Set a GPIO output channel to on or off:

```python
# Turn on
await kvm.gpio.switch("relay1", True)

# Turn off
await kvm.gpio.switch("relay1", False)
```

## Pulse

Send a pulse to a GPIO channel:

```python
# Pulse with server-default duration
await kvm.gpio.pulse("relay1")

# Pulse with custom duration (seconds)
await kvm.gpio.pulse("relay1", delay=0.5)
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        state = await kvm.gpio.get_state()

        # Toggle all output channels
        for name, ch in state.outputs.items():
            if ch.online and not ch.busy:
                await kvm.gpio.switch(name, not ch.state)

asyncio.run(main())
```
