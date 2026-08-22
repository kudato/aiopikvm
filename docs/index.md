# aiopikvm

Async Python client for the [PiKVM](https://pikvm.org) API.

Targets **kvmd 4.206** or later — see
[PiKVM version](getting-started/installation.md#pikvm-version).

## Key features

- **Full async/await API** built on [httpx](https://www.python-httpx.org/)
- **11 API resources**: ATX, HID, MSD, GPIO, Streamer, Media, Switch, Redfish, Prometheus, System, Auth
- **WebSocket client** for realtime events and low-latency HID input
- **Live video**: the MJPEG stream, H.264 frames off the media daemon, and
  WebRTC through Janus with the `webrtc` extra
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
        screen = await kvm.streamer.snapshot()
        with open("screen.jpeg", "wb") as f:
            f.write(screen.data)

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
- [Live Video](guide/video.md) — the MJPEG stream and H.264 over the media socket
- [WebRTC Video](guide/webrtc.md) — the low-latency path, through Janus
- [KVM Switch](guide/switch.md) — multi-port switching and EDID management
- [Redfish BMC](guide/redfish.md) — DMTF Redfish compatibility
- [Prometheus Metrics](guide/prometheus.md) — metrics export
- [System Info & Logs](guide/system.md) — device info and log streaming
- [WebSocket](guide/websocket.md) — realtime events and HID input
- [Error Handling](guide/error-handling.md) — exception hierarchy and patterns

## API reference

Auto-generated from source code docstrings:

- [PiKVM Client](reference/client.md) — main client class
- [WebSocket](reference/ws.md) — WebSocket client
- [Media WebSocket](reference/media-ws.md) — the video socket
- [WebRTC Session](reference/webrtc.md) — the Janus signalling session
- [Models](reference/models.md) — Pydantic response models
- [Exceptions](reference/exceptions.md) — exception hierarchy
- [Resources](reference/resources/auth.md) — all API resource classes
