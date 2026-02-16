# Error Handling

aiopikvm provides a structured exception hierarchy for precise error handling.

## Exception hierarchy

```
PiKVMError
├── APIError
│   └── AuthError
├── ConnectError
├── ConnectionTimeoutError
└── WebSocketError
```

All exceptions inherit from `PiKVMError`, so you can catch all aiopikvm errors with a single handler.

## Exception types

| Exception | When raised |
|-----------|-------------|
| `PiKVMError` | Base exception for all errors; also raised when accessing resources before entering async context |
| `APIError` | PiKVM returned an HTTP error (>= 400) or the JSON body has `"ok": false` |
| `AuthError` | Authentication failed (HTTP 401 or 403) |
| `ConnectError` | Failed to connect to PiKVM (network unreachable, DNS failure, etc.) |
| `ConnectionTimeoutError` | Request timed out |
| `WebSocketError` | WebSocket connection or communication error |

## APIError details

`APIError` includes a `status_code` attribute:

```python
from aiopikvm import APIError

try:
    await kvm.atx.get_state()
except APIError as exc:
    print(f"Status: {exc.status_code}")
    print(f"Message: {exc}")
```

!!! note
    When `APIError` is raised from the JSON body (`"ok": false`), `status_code` is `0`.

## Usage patterns

### Catch all errors

```python
from aiopikvm import PiKVMError

try:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        await kvm.atx.power_on()
except PiKVMError as exc:
    print(f"PiKVM error: {exc}")
```

### Catch specific errors

```python
from aiopikvm import AuthError, ConnectError, ConnectionTimeoutError, APIError

try:
    await kvm.atx.get_state()
except AuthError:
    print("Invalid credentials")
except ConnectError:
    print("Cannot reach PiKVM")
except ConnectionTimeoutError:
    print("Request timed out")
except APIError as exc:
    print(f"API error {exc.status_code}: {exc}")
```

### WebSocket errors

```python
from aiopikvm import WebSocketError

try:
    async with kvm.ws() as ws:
        async for event in ws.events():
            print(event)
except WebSocketError as exc:
    print(f"WebSocket error: {exc}")
```

### Context not entered

```python
from aiopikvm import PiKVM, PiKVMError

kvm = PiKVM("https://pikvm.local")

try:
    _ = kvm.atx  # Raises PiKVMError — async context not entered
except PiKVMError as exc:
    print(exc)
```

## Full example

```python
import asyncio
from aiopikvm import (
    PiKVM,
    PiKVMError,
    AuthError,
    ConnectError,
    ConnectionTimeoutError,
)

async def main():
    try:
        async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
            state = await kvm.atx.get_state()
            print(f"Power: {state.leds.power}")
    except AuthError:
        print("Check your username and password")
    except ConnectError:
        print("Cannot connect to PiKVM — check the URL and network")
    except ConnectionTimeoutError:
        print("Connection timed out — PiKVM may be busy")
    except PiKVMError as exc:
        print(f"Unexpected error: {exc}")

asyncio.run(main())
```
