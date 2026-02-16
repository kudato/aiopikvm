# aiopikvm

Async Python client for the [PiKVM](https://pikvm.org) API.

## Key features

- **Full async/await API** built on [httpx](https://www.python-httpx.org/)
- **9 API resources**: ATX, HID, MSD, GPIO, Streamer, Switch, Redfish, Prometheus, Auth
- **WebSocket client** for realtime events and low-latency HID input
- **Pydantic v2** response models with full type safety
- **PEP 561** compatible — works with mypy strict mode
- **TOTP** two-factor authentication support

## Quick example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        # Check host power state
        state = await kvm.atx.get_state()
        if not state.leds.power:
            await kvm.atx.power_on()

        # Type text via HID
        await kvm.hid.type_text("Hello from aiopikvm!")

        # Take a screenshot
        snapshot = await kvm.streamer.snapshot()
        with open("screen.jpeg", "wb") as f:
            f.write(snapshot)

asyncio.run(main())
```

## Getting started

New to aiopikvm? Start here:

- [Installation](getting-started/installation.md) — install the package
- [Quick Start](getting-started/quickstart.md) — first steps with every resource
- [Configuration](getting-started/configuration.md) — constructor parameters and client lifecycle

## User guide

Detailed guides for each API resource:

- [ATX Power Control](guide/atx.md) — power on/off, reset, status LEDs
- [HID Keyboard & Mouse](guide/hid.md) — typing, key events, mouse control
- [Mass Storage (MSD)](guide/msd.md) — virtual drives and image upload
- [GPIO Channels](guide/gpio.md) — read/write GPIO state
- [Streamer & OCR](guide/streamer.md) — screenshots and text recognition
- [KVM Switch](guide/switch.md) — multi-port switching and EDID management
- [Redfish BMC](guide/redfish.md) — DMTF Redfish compatibility
- [Prometheus Metrics](guide/prometheus.md) — metrics export
- [WebSocket](guide/websocket.md) — realtime events and HID input
- [Error Handling](guide/error-handling.md) — exception hierarchy and patterns

## API reference

Auto-generated from source code docstrings:

- [PiKVM Client](reference/client.md) — main client class
- [WebSocket](reference/ws.md) — WebSocket client
- [Models](reference/models.md) — Pydantic response models
- [Exceptions](reference/exceptions.md) — exception hierarchy
- [Resources](reference/resources/auth.md) — all API resource classes
