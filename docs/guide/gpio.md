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

The returned `GPIOState` mirrors what kvmd sends — the channel readings live
under `state`, and `inputs`/`outputs` above are shortcuts to them:

| Field | Type | Description |
|-------|------|-------------|
| `state.inputs` | `dict[str, GPIOInput]` | Input channel readings |
| `state.outputs` | `dict[str, GPIOChannel]` | Output channel readings |
| `model.scheme` | `GPIOScheme` | Configured channels: driver, pin, pulse limits |
| `model.view` | `GPIOView` | Layout of the GPIO widget in the web UI |

The scheme says what a channel can do, which the readings do not:

```python
scheme = state.model.scheme.outputs["relay1"]
print(f"Driver: {scheme.hw.driver}, pin {scheme.hw.pin}")
print(f"Switchable: {scheme.switch}")
print(f"Pulse: {scheme.pulse.min_delay}-{scheme.pulse.max_delay} s")
```

A `pulse.delay` of `0` means the channel cannot be pulsed — `pulse()` on it
fails with `GpioPulseNotSupported`.

## Switch output

Set a GPIO output channel to on or off:

```python
# Turn on
await kvm.gpio.switch("relay1", True)

# Turn off
await kvm.gpio.switch("relay1", False)
```

By default kvmd answers as soon as the switch starts. A busy channel is
reported either way — `BusyError` (HTTP 409) — but anything that goes wrong
*after* the action begins, an offline driver above all, is written to kvmd's
log and never reaches the caller. Pass `wait=True` to have the request block
until the channel has actually switched, so those failures surface too:

```python
from aiopikvm import APIError, BusyError

try:
    await kvm.gpio.switch("relay1", True, wait=True)
except BusyError:
    print("The channel is busy with another action")
except APIError as exc:
    print(f"The switch itself failed: {exc.error}")
```

Unlike the ATX calls, which wait by default, `wait` here defaults to `False`
to match kvmd's own default.

!!! warning "`state` is not the pin while `busy` is set"
    kvmd does not read the hardware for a channel that has an action
    running. It returns `state: false` and `online: true` for the duration,
    whatever the pin is doing — so an output that is on reads as off for the
    whole of any action against it. Read `busy` first; `state` means nothing
    until it clears.

    That is easy to walk into precisely because `wait` defaults to `False`:
    the call returns as the action starts, and a read taken straight
    afterwards lands inside the window.

    ```python
    await kvm.gpio.switch("relay1", True)
    ch = (await kvm.gpio.get_state()).state.outputs["relay1"]
    print(ch.state)  # False — the action is still running

    await kvm.gpio.switch("relay1", True, wait=True, timeout=30.0)
    ch = (await kvm.gpio.get_state()).state.outputs["relay1"]
    print(ch.state)  # True
    ```

    Switching a channel to the state it already has is not a shortcut past
    this: kvmd runs the action anyway, busy window and all.

## Pulse

Send a pulse to a GPIO channel:

```python
# Pulse with server-default duration
await kvm.gpio.pulse("relay1")

# Pulse with custom duration (seconds)
await kvm.gpio.pulse("relay1", delay=0.5)

# Wait for a long pulse to finish, and widen the timeout to match
await kvm.gpio.pulse("relay1", delay=30.0, wait=True, timeout=60.0)
```

kvmd clamps `delay` to the channel's `min_delay`/`max_delay` from the scheme;
`0` means the channel default, same as omitting it.

!!! note

    `GPIOState` describes the full `GET /api/gpio` response. The `gpio` events
    on the WebSocket carry partial updates — often just the channels that
    changed, without the `model` block — so they do not validate against it.

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
