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
├── WebRTCError
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
| `RedirectError` | PiKVM answered with a redirect (3xx) and the client was not created with `follow_redirects=True` — or it was, and the redirects formed a loop, the one case where `status_code` is `0` |
| `ResponseError` | The response was not the documented JSON envelope, did not match the model for that endpoint, or did not survive its `Content-Encoding` |
| `ConfigurationError` | The client cannot use what it was given, and nothing was sent: an unusable URL, proxy or credentials — including a TOTP code that is not ASCII, which is only known once the code has been produced — a call with no parameters at all, or a value kvmd's own encoding would silently mangle: a shortcut key holding a comma or whitespace, a key name that will not fit a binary WebSocket frame |
| `ConnectError` | Failed to connect to PiKVM, or the connection broke mid-request |
| `ConnectionTimeoutError` | Request timed out |
| `WebRTCError` | The Janus gateway or its ustreamer plugin refused, the negotiation never completed, or the session was used while not open — before its `async with` block, or after it. Once the upgrade is through, `/janus/ws` speaks Janus's own protocol, so this carries `code` and `reason` — Janus's numbering or the plugin's — instead of an HTTP status; `code` is `0` when the failure had none. The upgrade itself is not this: kvmd's auth chain sits in front of Janus, so a refused handshake raises `AuthError`/`APIError` like any request, and the signalling socket failing raises `WebSocketError` |
| `WebSocketError` | The WebSocket could not be opened, or it broke instead of closing cleanly. A handshake kvmd itself refuses raises `AuthError`/`APIError` instead |

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
    `status_code` is `0` when there was no single status to report: an error
    kvmd put in the body of an HTTP 200 (`"ok": false`), or a redirect loop
    the client gave up on.

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

A redirect is reported instead of being followed, because following one that
leaves the device hands the `X-KVMD-User` / `X-KVMD-Passwd` headers to
wherever it points. The session token under `auth="cookie"` is scoped to the
device host and stays behind, so it follows a redirect only within it:

```python
from aiopikvm import RedirectError

try:
    await kvm.atx.get_state()
except RedirectError as exc:
    print(exc)  # HTTP 301: PiKVM redirected to https://pikvm.local/api/atx
```

The usual cause is an `http://` base URL that PiKVM's nginx redirects to
`https://` — by which point the password or the token has already gone out in
cleartext, so fix the URL rather than the symptom. Pass
`follow_redirects=True` to the client if you have a proxy that legitimately
redirects.

## Values the type checker catches

Several parameters take one of a short list of names kvmd knows, and a name
outside it is an HTTP 400 — a mistake that otherwise only shows up when the
call runs. Those parameters carry a literal type, so a typo is a type error
instead:

| Parameter | Type | Values |
|---|---|---|
| `hid.set_params(keyboard_output=…)` | `KeyboardOutput` | `usb`, `ps2`, `disabled` |
| `hid.set_params(mouse_output=…)` | `MouseOutput` | `usb`, `usb_win98`, `usb_rel`, `ps2`, `disabled` |
| `hid.send_mouse_button()`, `ws.send_mouse_button()` | `MouseButton` | `left`, `right`, `middle`, `up`, `down` |
| `msd.download(compress=…)` | `Compression` | `""`, `none`, `lzma`, `zstd` |
| `switch.atx_power()` | `ATXAction` | `on`, `off`, `off_hard`, `reset_hard` |
| `switch.atx_click()` | `ATXButton` | `power`, `power_long`, `reset` |
| `redfish.reset()` | `ResetType` | `On`, `ForceOn`, `ForceOff`, `GracefulShutdown`, `ForceRestart`, `PushPowerButton` |

Each lives in the resource module whose parameter it belongs to, so a
variable holding one can be annotated:

```python
from aiopikvm import MouseButton

button: MouseButton = "left"
await kvm.hid.send_mouse_button(button)
```

The annotation is the point: without it mypy infers `str` for that variable
and refuses the call. Write it where the value is written down, not at the
call site.

For a name that arrives at runtime — out of a config file, off a UI — the
values are on the type:

```python
from typing import cast, get_args
from aiopikvm import MouseButton

if name not in get_args(MouseButton.__value__):
    raise ValueError(f"kvmd has no mouse button named {name!r}")
await kvm.hid.send_mouse_button(cast(MouseButton, name))
```

The `cast` is not optional: `get_args()` is typed as returning
`tuple[Any, ...]`, so the check above narrows nothing for mypy even though
it settles the question at runtime. What makes the cast honest is the line
before it.

Two things these types deliberately do not do. They do not enforce anything
at runtime: the client sends what it is handed, so a value some later kvmd
understands still goes through with a `cast` or a
`# type: ignore[arg-type]`. And they stay out of the response models, where
a literal type would turn a value this release has not seen into a
`ResponseError` instead of a string the caller can look at and decide about.

`ResetType` is matched by kvmd as written. Every other list in the table is
lowercased before matching, so a device would also take `"USB"` or
`"Left"` — only the canonical spelling is typed.

Key names are the one vocabulary left as plain `str`, and they are
case-sensitive like `ResetType`. There are 126 of them, and a key is
usually computed rather than written out, so they are a runtime set
instead: see [`KEY_NAMES`](hid.md#key-names).

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

The upgrade to `/api/ws` goes through the same auth chain as every REST call
and is refused with an ordinary HTTP response, so a refused handshake raises
`AuthError` — not `WebSocketError`, which is reserved for a socket that never
opened or that broke:

```python
from aiopikvm import APIError, AuthError, WebSocketError

try:
    async with kvm.ws() as ws:
        async for event in ws.events():
            print(event)
except AuthError as exc:
    print(f"kvmd refused the credentials: HTTP {exc.status_code}")
except APIError as exc:
    print(f"kvmd refused the upgrade: HTTP {exc.status_code} {exc.error}")
except WebSocketError as exc:
    print(f"the connection failed or was lost: {exc}")
```

`events()` ends quietly when either side closes the connection cleanly and
raises `WebSocketError` when it breaks instead, so a loop that simply finishes
never hides a dropped connection.

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
