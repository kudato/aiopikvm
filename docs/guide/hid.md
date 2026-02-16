# HID Keyboard & Mouse

The HID resource provides keyboard and mouse control over the PiKVM's HID interface.

## Get state

```python
state = await kvm.hid.get_state()
print(f"Online: {state.online}")
print(f"Busy: {state.busy}")
print(f"Keyboard connected: {state.keyboard.connected}")
print(f"Mouse connected: {state.mouse.connected}")
print(f"Mouse absolute: {state.mouse.absolute}")
```

## Type text

Send a string as keyboard input:

```python
await kvm.hid.type_text("Hello from aiopikvm!")

# Limit characters per request
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

# With delay between key events (milliseconds)
await kvm.hid.send_shortcut("ControlLeft", "KeyC", wait=50)
```

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
```

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
print(keymaps)
```

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
