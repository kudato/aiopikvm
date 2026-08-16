# Error Handling

aiopikvm provides a structured exception hierarchy for precise error handling.

## Exception hierarchy

```
PiKVMError
├── APIError
│   ├── AuthError
│   ├── BusyError
│   ├── UnavailableError
│   ├── RedirectError
│   └── ResponseError
├── ConfigurationError
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
| `BusyError` | PiKVM is busy with another operation (HTTP 409); the same call usually succeeds once it finishes |
| `UnavailableError` | The subsystem is disabled in the kvmd config or offline (HTTP 503) |
| `RedirectError` | PiKVM answered with a redirect (3xx) and the client was not created with `follow_redirects=True` |
| `ResponseError` | The response was not the documented JSON envelope, or did not match the model for that endpoint |
| `ConfigurationError` | The URL has no usable scheme, or the credentials cannot be sent in HTTP headers |
| `ConnectError` | Failed to connect to PiKVM, or the connection broke mid-request |
| `ConnectionTimeoutError` | Request timed out |
| `WebSocketError` | WebSocket connection or communication error |

## APIError details

`APIError` carries the HTTP status and the error block kvmd puts in the body
(`{"ok": false, "result": {"error": "AtxIsBusyError", "error_msg": "..."}}`).
The class name is the kvmd exception, so it is subsystem-specific:

```python
from aiopikvm import APIError

try:
    await kvm.atx.click_power()
except APIError as exc:
    print(f"Status: {exc.status_code}")   # 409
    print(f"Class: {exc.error}")          # AtxIsBusyError
    print(f"Message: {exc.error_msg}")    # Performing another ATX operation, ...
```

`error` and `error_msg` are empty strings when the response carried no kvmd
error block — for example when a reverse proxy answered instead of kvmd.

!!! note
    When `APIError` is raised from the JSON body (`"ok": false`), `status_code` is `0`.

## Retrying a busy device

ATX, MSD and GPIO reject a request while an earlier operation is still running.
That is a `BusyError`, and it is the one failure worth retrying as-is:

```python
from aiopikvm import BusyError

for attempt in range(5):
    try:
        await kvm.atx.click_power()
        break
    except BusyError:
        await asyncio.sleep(1)
```

## Redirects

A redirect is reported instead of being followed, because following it resends
the `X-KVMD-User` / `X-KVMD-Passwd` headers to wherever it points:

```python
from aiopikvm import RedirectError

try:
    await kvm.atx.get_state()
except RedirectError as exc:
    print(exc)  # HTTP 301: PiKVM redirected to https://pikvm.local/api/atx
```

The usual cause is an `http://` base URL that PiKVM's nginx redirects to
`https://` — by which point the password has already gone out in cleartext, so
fix the URL rather than the symptom. Pass `follow_redirects=True` to the
client if you have a proxy that legitimately redirects.

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
from aiopikvm import (
    APIError,
    AuthError,
    BusyError,
    ConnectError,
    ConnectionTimeoutError,
    ResponseError,
    UnavailableError,
)

try:
    await kvm.atx.click_power()
except AuthError:
    print("Invalid credentials")
except BusyError:
    print("PiKVM is busy — retry in a moment")
except UnavailableError:
    print("The subsystem is offline")
except ResponseError as exc:
    print(f"Cannot parse this kvmd version: {exc}")
except ConnectError:
    print("Cannot reach PiKVM")
except ConnectionTimeoutError:
    print("Request timed out")
except APIError as exc:
    print(f"API error {exc.status_code}: {exc}")
```

Order matters: `BusyError`, `UnavailableError`, `ResponseError`, `AuthError`
and `RedirectError` all inherit from `APIError`, so a bare `except APIError`
first would swallow them.

A subsystem that is disabled in the kvmd config does **not** produce
`UnavailableError`: kvmd answers HTTP 400 with its own class name, so it
arrives as a plain `APIError`. Tell them apart by `exc.error` — for example
`"AtxDisabledError"` — or check the subsystem state first, where `enabled`
says so without a failed call.

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
