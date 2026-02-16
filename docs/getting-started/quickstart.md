# Quick Start

## Basic usage

aiopikvm uses an async context manager to manage the HTTP session:

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        state = await kvm.atx.get_state()
        print(f"Power: {state.leds.power}")

asyncio.run(main())
```

## Available resources

| Resource | Property | Description |
|----------|----------|-------------|
| ATX | `kvm.atx` | Host power control |
| HID | `kvm.hid` | Keyboard and mouse input |
| MSD | `kvm.msd` | Virtual mass storage |
| GPIO | `kvm.gpio` | GPIO channel control |
| Streamer | `kvm.streamer` | Screenshots and OCR |
| Switch | `kvm.switch` | Multi-port KVM switching |
| Redfish | `kvm.redfish` | DMTF BMC interface |
| Prometheus | `kvm.prometheus` | Metrics export |
| Auth | `kvm.auth` | Session authentication |

## Examples by resource

### ATX — power control

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    state = await kvm.atx.get_state()
    if not state.leds.power:
        await kvm.atx.power_on()
```

### HID — keyboard and mouse

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    # Type text
    await kvm.hid.type_text("Hello!")

    # Send a keyboard shortcut
    await kvm.hid.send_shortcut("ControlLeft", "KeyA")

    # Move mouse
    await kvm.hid.send_mouse_move(500, 300)
```

### Streamer — screenshots

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    jpeg_bytes = await kvm.streamer.snapshot()
    with open("screen.jpeg", "wb") as f:
        f.write(jpeg_bytes)

    # OCR — read text from screen
    text = await kvm.streamer.ocr()
    print(text)
```

### WebSocket — realtime events

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    async with kvm.ws() as ws:
        await ws.send_key("KeyA", state=True)
        await ws.send_key("KeyA", state=False)

        async for event in ws.events():
            print(event)
```

## Next steps

- [Configuration](configuration.md) — constructor parameters and advanced setup
- [User Guide](../guide/atx.md) — detailed guides for each resource
