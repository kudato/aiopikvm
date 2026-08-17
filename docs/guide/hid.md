# HID Keyboard & Mouse

The HID resource provides keyboard and mouse control over the PiKVM's HID interface.

## Get state

```python
state = await kvm.hid.get_state()
print(f"Online: {state.online}")
print(f"Busy: {state.busy}")
print(f"Keyboard online: {state.keyboard.online}")
print(f"Caps Lock: {state.keyboard.leds.caps}")
print(f"Mouse online: {state.mouse.online}")
print(f"Mouse absolute: {state.mouse.absolute}")
print(f"Mouse outputs: {state.mouse.outputs.available}")
print(f"Jiggler: {state.jiggler.enabled}")
```

`state.connected` reports whether the target host has the HID plugged in.
Only the MCU-based backends can tell — `otg`, `ch9329` and `bt` report
`None`. See [Connection control](#connection-control) for what that does and
does not imply.

## Idle time

Seconds since the last key press or mouse movement kvmd delivered, from
any of its clients — the web UI, another script, or this one. Input typed
on a keyboard plugged straight into the host does not reset it:

```python
if await kvm.hid.get_inactivity() > 300:
    print("Nobody has used the PiKVM for five minutes")
```

## Type text

Send a string as keyboard input:

```python
await kvm.hid.type_text("Hello from aiopikvm!")

# Type with a specific layout instead of the device-wide default
await kvm.hid.type_text("Hello", keymap="en-us")

# Slow down for firmware that drops fast input (0 to 5 seconds per key)
await kvm.hid.type_text("Hello", delay=0.05)
```

kvmd answers only once the whole string is typed, so a large `delay` or a
long text needs a wider timeout than the 10-second client default:

```python
await kvm.hid.type_text(bios_config, delay=0.5, timeout=120)
```

`limit` is server-side truncation, not chunking: kvmd types the first
`limit` characters and discards the rest. The default is `0` (type
everything).

```python
# Type at most 50 characters and drop the remainder
await kvm.hid.type_text("Long text...", limit=50)
```

## Send key events

```python
# Press and release a key
await kvm.hid.send_key("KeyA")

# Press only (hold)
await kvm.hid.send_key("KeyA", state=True)

# Release only
await kvm.hid.send_key("KeyA", state=False)
```

### Key names

The names are kvmd's, and they are the ones a browser reports in
`KeyboardEvent.code` — `"KeyA"`, not `"a"`; `"Digit1"`, not `"1"`. Matching
is case-sensitive, so `"keya"` is refused like any other name kvmd does not
know.

`KEY_NAMES` holds all 115 of them:

```python
from aiopikvm.resources.hid import KEY_NAMES

if key not in KEY_NAMES:
    raise ValueError(f"kvmd has no key named {key!r}")
await kvm.hid.send_key(key)
```

Checking first is worth it for a name that came from somewhere you do not
control, because the two transports fail differently and neither is loud:
an HTTP call raises `APIError` with HTTP 400, while a key sent over the
[WebSocket](websocket.md) is dropped inside kvmd's handler with no answer at
all — nothing distinguishes a typo from a keystroke that landed.

kvmd exposes the table through no endpoint, so `KEY_NAMES` is a copy: it was
read off a device running kvmd 4.186 and is checked against that capture by
the test suite. A device on another version may know names it does not list,
which is why nothing in the client enforces it — a name outside the set is
sent as given.

## Keyboard shortcuts

```python
# Ctrl+A
await kvm.hid.send_shortcut("ControlLeft", "KeyA")

# Ctrl+Alt+Delete
await kvm.hid.send_shortcut("ControlLeft", "AltLeft", "Delete")
```

The server presses the keys in order and releases them in reverse order,
with a fixed 50 ms delay between events.

## Mouse control

### Move mouse

```python
# Move to absolute coordinates
await kvm.hid.send_mouse_move(500, 300)

# Relative movement
await kvm.hid.send_mouse_relative(10, -5)
```

### Mouse buttons

```python
# Click (press and release)
await kvm.hid.send_mouse_button("left")

# Press only
await kvm.hid.send_mouse_button("left", state=True)

# Release only
await kvm.hid.send_mouse_button("left", state=False)

# Right click
await kvm.hid.send_mouse_button("right")
```

### Mouse wheel

```python
# Scroll up
await kvm.hid.send_mouse_wheel(0, -5)

# Scroll down
await kvm.hid.send_mouse_wheel(0, 5)

# Horizontal scroll
await kvm.hid.send_mouse_wheel(3, 0)
```

## HID parameters

```python
# Set keyboard output type
await kvm.hid.set_params(keyboard_output="usb")

# Set mouse output type
await kvm.hid.set_params(mouse_output="usb_rel")

# Toggle the mouse jiggler, which keeps the host from going idle
await kvm.hid.set_params(jiggler=True)
```

Valid output names come from the state: `state.keyboard.outputs.available`
and `state.mouse.outputs.available`. Either list can be empty — an OTG
keyboard offers no choice at all, while its mouse still switches between
`usb` and `usb_rel`.

## Connection control

`set_connected()` unplugs the emulated keyboard and mouse from the target
host, and plugs them back in. **Only the MCU-based backends do it** — the
ones driving a separate microcontroller, `hid.type` set to `serial` or
`spi` in the kvmd config. Under `otg`, `ch9329` or `bt` the call lands on a
base implementation that discards its argument, so kvmd answers 200 and
nothing happens. (The device these docs were verified against, a v3, runs
`otg`.)

Nothing in the response says which of the two happened, so read the state —
in the one direction it is good for:

```python
state = await kvm.hid.get_state()
if state.connected is not None:
    # This backend implements it: the host stops seeing the keyboard.
    await kvm.hid.set_connected(False)
    await asyncio.sleep(2)
    await kvm.hid.set_connected(True)
```

`connected` being `None` is *not* proof of the opposite. An MCU backend
reports `None` too until its microcontroller has answered with a status word
carrying the flag, so a board that is merely offline, or whose firmware
answers the shorter pong, looks exactly like one that cannot unplug
anything. `state.online` rules out the offline board; the firmware that
never sends the flag cannot be told apart at all.

The change travels to the microcontroller through a queue and the call
returns as soon as it is queued. It also empties that queue on the way in,
so keystrokes sent a moment earlier and not yet delivered are dropped with
it — and so is the disconnect itself, if a reconnect follows before the
queue has been read. That is what the sleep above is for: back to back, the
two calls are a disconnect the host never notices, or never receives at all.

`reset()` is a different matter. Every backend overrides it, but what it
does differs:

```python
await kvm.hid.reset()
```

| `hid.type` | What `reset()` does |
|---|---|
| `otg` | Drops the queued input and releases every held key and button |
| `bt` | The same, then drops the Bluetooth clients — unpaired, unless `unpair_on_close` is off, so the host has to pair again |
| `serial`, `spi` | Resets the microcontroller; queued input survives |
| `ch9329` | Nothing observable: the reset request is commented out in kvmd 4.186, leaving an internal busy flag `get_state()` never reports |

Under `otg` that makes it the way out of a modifier left stuck by a script
that died mid-shortcut.

## Keymaps

```python
keymaps = await kvm.hid.get_keymaps()
print(f"Default: {keymaps.default}")
print(f"Available: {', '.join(keymaps.available)}")
```

The names are what `type_text(keymap=...)` accepts. The device-wide default
is set in the kvmd config and is not necessarily `en-us`.

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        # Type credentials into a login form
        await kvm.hid.type_text("admin")
        await kvm.hid.send_key("Tab")
        await kvm.hid.type_text("password123")
        await kvm.hid.send_key("Enter")

asyncio.run(main())
```
