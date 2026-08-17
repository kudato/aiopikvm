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

# Press, and have kvmd release it in the same event
await kvm.hid.send_key("KeyA", state=True, finish=True)
```

### `finish`, and the keys it does not release

A held key is held until a release arrives, and a script that dies between
the two never sends one — the device keeps typing `aaaaaaa` at whatever
was on screen. `finish=True` asks kvmd to send the release itself, straight
after the press and before it reads anything else, which is the one
keystroke a lost connection cannot interrupt halfway.

kvmd exempts the modifiers, because holding those is what they are for:

| Exempt | Released by `finish` |
|---|---|
| `ShiftLeft`, `ShiftRight` | every other key |
| `ControlLeft`, `ControlRight` | |
| `AltLeft`, `AltRight` | |
| `MetaLeft`, `MetaRight` | |
| `PrintScreen`, which kvmd counts as one for `Alt+SysRq` | |

Asking for it on one of those nine presses the key and leaves it held, with
nothing said either way. So does asking for it on a device running kvmd
older than 4.33, which does not read the parameter at all.

!!! note
    `send_key("KeyA")` with no `state` **is** the press-and-release above:
    kvmd 4.33 replaced the two events it used to send with a single press
    carrying `finish`. The nine keys are exempt there too, so
    `send_key("ShiftLeft")` presses Shift and leaves it down — where kvmd
    4.32 and earlier released it. Pass `state=False` to let it up.

### Key names

The names are kvmd's, and they are written the way a browser reports them in
`KeyboardEvent.code` — `"KeyA"`, not `"a"`; `"Digit1"`, not `"1"`. Matching
is case-sensitive, so `"keya"` is refused like any other name kvmd does not
know. The two sets are not the same, though: kvmd knows 115 names and the
DOM defines around 200, so forwarding a browser's `code` straight through
will eventually hand it something it has no entry for — `F13` and
`NumpadEqual` are real `code` values with no key behind them here.

`KEY_NAMES` holds every one kvmd does know:

```python
from aiopikvm.resources.hid import KEY_NAMES

if key not in KEY_NAMES:
    raise ValueError(f"kvmd has no key named {key!r}")
await kvm.hid.send_key(key)
```

Over HTTP the check is a convenience — `send_key()` raises `APIError` with
HTTP 400, and the message names the offending key unless it is longer than
16 characters, which kvmd's validator refuses on length alone. Over the
[WebSocket](websocket.md) it is the only signal there is: kvmd drops the
frame inside its handler and answers nothing, so a typo and a keystroke that
landed look exactly alike.

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

The names are the `MouseButton` type
([the values](error-handling.md#values-the-type-checker-catches)). Two of
them read oddly: `up` and `down` are the side buttons a browser reports as
back and forward, not wheel directions — the wheel is below.

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

The output names are typed as `KeyboardOutput` and `MouseOutput`
([the values](error-handling.md#values-the-type-checker-catches)), so a typo
is a type error rather than an HTTP 400. That is kvmd's validator, though,
and passing it is not the same as taking effect. kvmd checks the name against
the fixed list whatever backend is running, and then hands it to a backend
that may have no use for it: only the MCU backends act on `keyboard_output`
at all, while `otg`, `ch9329` and `bt` discard it and answer 200.

What the running backend offers is in the state:
`state.keyboard.outputs.available` and `state.mouse.outputs.available`.
Either can be empty — an OTG keyboard offers no choice at all, while its
mouse still moves between `usb` and `usb_rel`.

A name kvmd knows but the backend does not advertise is still not an error,
and what becomes of it differs: `otg` ignores it under a 200, while `ch9329`
advertises two names and acts on all five, taking everything but `usb` as its
relative mouse. Read the state back rather than assume the name was applied
as asked — `state.mouse.outputs.active` names the mouse in use and
`state.mouse.absolute` says whether it reports positions or movement.

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
