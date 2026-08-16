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
Only MCU-based backends can tell — it is `None` on OTG.

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

```python
# Disconnect HID
await kvm.hid.set_connected(False)

# Reconnect HID
await kvm.hid.set_connected(True)

# Reset HID subsystem
await kvm.hid.reset()
```

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
