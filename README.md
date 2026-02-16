# aiopikvm

[![CI](https://github.com/kudato/aiopikvm/actions/workflows/ci.yml/badge.svg)](https://github.com/kudato/aiopikvm/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aiopikvm)](https://pypi.org/project/aiopikvm/)
[![Python](https://img.shields.io/pypi/pyversions/aiopikvm)](https://pypi.org/project/aiopikvm/)
[![License](https://img.shields.io/github/license/kudato/aiopikvm)](https://github.com/kudato/aiopikvm/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue)](https://kudato.github.io/aiopikvm)

Async Python client for the [PiKVM](https://pikvm.org) API.

## Installation

```bash
pip install aiopikvm
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv add aiopikvm
```

## Quick start

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        # ATX power control
        state = await kvm.atx.get_state()
        if not state.leds.power:
            await kvm.atx.power_on()

        # HID — type text
        await kvm.hid.type_text("Hello from aiopikvm!")

        # Streamer — take a screenshot
        snapshot = await kvm.streamer.snapshot()
        with open("screen.jpeg", "wb") as f:
            f.write(snapshot)

asyncio.run(main())
```

## Features

### API resources

| Resource | Description |
|---|---|
| **ATX** | Host power control (power on/off, reset, status LEDs) |
| **HID** | Keyboard and mouse input, text typing, keymaps |
| **MSD** | Virtual mass storage drives, image upload |
| **GPIO** | GPIO channel read/write control |
| **Streamer** | Video snapshots, OCR |
| **Switch** | Multi-port KVM switching, EDID management |
| **Redfish** | DMTF Redfish BMC compatibility interface |
| **Prometheus** | Metrics export in Prometheus format |
| **WebSocket** | Realtime events, keyboard/mouse input via WebSocket |

### Highlights

- Full async/await API built on [httpx](https://www.python-httpx.org/)
- Response models powered by [Pydantic](https://docs.pydantic.dev/) v2
- Custom exception hierarchy for precise error handling
- External `httpx.AsyncClient` support for advanced use cases
- TOTP two-factor authentication support
- WebSocket client for realtime events and low-latency HID input
- Fully typed (PEP 561, mypy strict)

## Configuration

```python
from aiopikvm import PiKVM

kvm = PiKVM(
    "https://pikvm.local",
    user="admin",          # default: "admin"
    passwd="secret",
    totp="123456",         # optional TOTP code
    verify_ssl=False,      # default: False (PiKVM uses self-signed certs)
    timeout=10.0,          # default timeout in seconds
)
```

### External httpx client

```python
import httpx
from aiopikvm import PiKVM

async with httpx.AsyncClient(verify=False) as http:
    async with PiKVM("https://pikvm.local", http_client=http) as kvm:
        state = await kvm.atx.get_state()
```

When an external client is provided, `PiKVM` does **not** close it on exit — the caller is responsible.

## WebSocket

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    async with kvm.ws() as ws:
        # Send keyboard input
        await ws.send_key("KeyA", state=True)
        await ws.send_key("KeyA", state=False)

        # Move mouse
        await ws.send_mouse_move(100, 200)

        # Iterate over realtime events
        async for event in ws.events():
            print(event)
```

## Documentation

Full documentation is available at [kudato.github.io/aiopikvm](https://kudato.github.io/aiopikvm).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

[MIT](LICENSE)
