# WebSocket

The WebSocket client connects to PiKVM's realtime event stream and provides low-latency HID input.

## Creating a connection

Use `kvm.ws()` to create a WebSocket connection:

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    async with kvm.ws() as ws:
        ...
```

### Connection parameters

```python
async with kvm.ws(
    stream=True,           # count as a video viewer (default, same as kvmd's)
    open_timeout=10.0,     # connection timeout
    close_timeout=10.0,    # close timeout
) as ws:
    ...
```

`stream` is a flag, not an index. kvmd counts the connected sessions that asked for
video and runs the streamer for as long as that count is above zero, so an open
socket is what keeps the video pipeline alive:

```python
async with kvm.ws():                        # streamer stays up
    print(await kvm.streamer.snapshot())    # ... so this has a picture to return
```

Pass `stream=False` only for a client that reads events and never looks at the
picture. With nothing else watching, the streamer stops, `StreamerState.streamer`
becomes `None` and `kvm.streamer.snapshot()` answers `UnavailableError`
(HTTP 503) — unless the device is configured with `kvmd.streamer.forever: true`,
which keeps it running regardless.

### Authentication

The socket authenticates with the `user` and `passwd` the client was built with,
not with a session token — `kvm.cookies` plays no part in the handshake.

kvmd applies the same auth chain to the upgrade as to the REST API, and refuses
it with an ordinary HTTP response, so the errors are the familiar ones:

```python
from aiopikvm import APIError, AuthError, WebSocketError

try:
    async with kvm.ws() as ws:
        ...
except AuthError as err:          # 401 no credentials, 403 rejected
    print(err.status_code, err.error_msg)
except APIError as err:           # anything else kvmd refused the upgrade with
    print(err.status_code)
except WebSocketError as err:     # DNS, TLS, timeout — the socket never opened
    print(err)
```

## Receiving events

Iterate over incoming events using `events()`:

```python
async with kvm.ws() as ws:
    async for event in ws.events():
        print(event["event_type"], event["event"])
```

Every frame is a `{"event_type": ..., "event": ...}` dictionary.

### The connection sequence

There is no single "initial state" message. kvmd sends:

1. `loop` — always first, carrying the kvmd version:
   `{"version": {"major": 4, "minor": 186}}`.
2. one event per subsystem with its current state, in **no guaranteed order** —
   broadcasts meant for every client interleave with them.
3. updates from then on, whenever anything changes.

So a client that needs a particular subsystem waits for its event rather than
reading the first message:

```python
state = {}
async with kvm.ws() as ws:
    async for event in ws.events():
        state[event["event_type"]] = event["event"]
        if {"atx", "hid", "streamer"} <= state.keys():
            break
```

### Event types

| `event_type` | Payload |
|---|---|
| `loop` | kvmd version; always the first frame |
| `atx` | power and LED state, same shape as `GET /api/atx` |
| `hid` | keyboard, mouse and jiggler state |
| `hid_keymaps` | available keymaps |
| `msd` | mass storage drive and storage state |
| `gpio` | GPIO scheme, view and pin state |
| `streamer` | streamer state, features, limits and parameters |
| `ocr` | whether OCR is enabled and which languages it has |
| `switch` | PiKVM Switch model, port state and summary |
| `info` | the `/api/info` subsystems, one at a time |
| `clients` | `{"count": N}` — how many connected sessions asked for video |
| `pong` | answer to [`ping()`](#ping) |

Two things a consumer has to expect:

- **Updates can be partial.** The first `streamer` event carries the whole
  state, later ones only the field that changed. `info` never sends a bundle at
  all — each event carries a single key such as `uptime` or `health`. Merge into
  what you already have instead of replacing it.
- **`clients` arrives unprompted**, broadcast to everyone whenever any session
  connects or disconnects, which is why it turns up in the middle of the initial
  burst.

### When the stream ends

The iteration finishes when either side closes the connection cleanly. A
connection that breaks instead — the device rebooting, kvmd restarting, the
network going away — raises `WebSocketError`, so a silent end of iteration is
never a lost connection:

```python
try:
    async for event in ws.events():
        handle(event)
except WebSocketError as err:
    print("reconnecting:", err)
```

## Keyboard input

```python
async with kvm.ws() as ws:
    # Press a key
    await ws.send_key("KeyA", state=True)

    # Release a key
    await ws.send_key("KeyA", state=False)
```

Key names are kvmd's web names — `KeyA`, `Digit1`, `ControlLeft`, `F5`. kvmd
holds a key down until the release arrives, and silently ignores a name it does
not know.

## Mouse input

### Move mouse

```python
async with kvm.ws() as ws:
    await ws.send_mouse_move(0, 0)          # centre of the screen
    await ws.send_mouse_move(-32768, -32768)  # top left corner
```

**The coordinates are not pixels.** kvmd works in a resolution-independent space
from `-32768` (left, top) to `32767` (right, bottom), so `0, 0` is the middle of
the screen and `send_mouse_move(500, 300)` lands a hair right of and below it.
Values outside the range are clamped by kvmd rather than rejected.

Converting from pixels needs the resolution the *target machine* is sending,
which `GET /api/streamer` reports — but only while the streamer is running, so
read it with the socket already open:

```python
async with kvm.ws() as ws:                  # keeps the streamer up
    state = await kvm.streamer.get_state()
    assert state.streamer is not None       # None once nothing is watching
    size = state.streamer.source.resolution

    def to_kvmd(x: int, y: int) -> tuple[int, int]:
        return (
            round(x / (size.width - 1) * 65535) - 32768,
            round(y / (size.height - 1) * 65535) - 32768,
        )

    await ws.send_mouse_move(*to_kvmd(960, 540))
```

Absolute positioning also needs the mouse in absolute mode; `usb_rel` and
`ps2` send relative motion instead, which this client does not expose yet
([#60](https://github.com/kudato/aiopikvm/issues/60)).

### Mouse buttons

```python
async with kvm.ws() as ws:
    # Press left button
    await ws.send_mouse_button("left", True)

    # Release left button
    await ws.send_mouse_button("left", False)
```

Valid names are `left`, `right`, `middle`, `up` (browser back) and `down`
(browser forward).

### Mouse wheel

```python
async with kvm.ws() as ws:
    await ws.send_mouse_wheel(0, -5)   # scroll down
    await ws.send_mouse_wheel(0, 5)    # scroll up
```

Deltas are steps in kvmd's own range, `-127` to `127`, clamped rather than
rejected — not the browser's pixel deltas. kvmd's web UI sends a single step per
gesture, sized by its scroll-rate setting (1 to 25, `5` by default) and negated,
so a scroll-down gesture reaches the device as `delta_y = -5`.

## Ping

```python
async with kvm.ws() as ws:
    await ws.ping()
```

This is kvmd's application-level ping: the answer comes back as a `pong` event
through `events()`, and `ping()` does not wait for it. Keeping the socket alive
needs neither — kvmd sends protocol-level pings on its own and the underlying
library answers them.

## Standalone usage

`PiKVMWebSocket` can also be used independently:

```python
from aiopikvm import PiKVMWebSocket

ws = PiKVMWebSocket(
    url="https://pikvm.local",
    user="admin",
    passwd="admin",
    verify_ssl=False,
    stream=True,
    open_timeout=10.0,
    close_timeout=10.0,
)

async with ws:
    async for event in ws.events():
        print(event)
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        async with kvm.ws() as ws:
            # Type "hello" via WebSocket HID
            for char in "hello":
                key = f"Key{char.upper()}"
                await ws.send_key(key, state=True)
                await ws.send_key(key, state=False)
                await asyncio.sleep(0.05)

            # Read a few events
            count = 0
            async for event in ws.events():
                print(event)
                count += 1
                if count >= 5:
                    break

asyncio.run(main())
```
