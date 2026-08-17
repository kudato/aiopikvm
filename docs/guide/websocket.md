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
    binary=False,          # send input as JSON events (default) or binary ops
    open_timeout=10.0,     # connection timeout
    close_timeout=10.0,    # close timeout
) as ws:
    ...
```

The socket inherits the client's `verify_ssl`, `proxy`, `trust_env` and
`follow_redirects`, so a private CA or a proxy is configured once and covers
both protocols — including when an external `http_client` was supplied, since
the socket does not go through httpx and keeps reading these from the `PiKVM`
constructor. Redirects are not followed by default for the same reason as
on the REST side: the upgrade carries the password in a header, and following
the redirect hands it to whatever the redirect points at.

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
from aiopikvm import APIError, AuthError, RedirectError, WebSocketError

try:
    async with kvm.ws() as ws:
        ...
except AuthError as err:          # 401 no credentials, 403 rejected
    print(err.status_code, err.error_msg)
except RedirectError as err:      # not followed: it would resend the password
    print(err)
except APIError as err:           # anything else kvmd refused the upgrade with
    print(err.status_code)
except WebSocketError as err:     # DNS, TLS, timeout — the socket never opened
    print(err)
```

A status means the same thing on both transports — `AuthError` for 401 and
403, `BusyError` for 409, `UnavailableError` for 503, `RedirectError` for 3xx —
because the REST client and the socket share one mapping.

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
   `{"version": {"major": 4, "minor": 186}}`. The client keeps it, so there is
   no need to catch the event to read it:

   ```python
   async with kvm.ws() as ws:
       await ws.ping()                 # or read one event; either fills it in
       print(ws.version)               # KvmdVersion(major=4, minor=186)
       if ws.version >= (4, 100):      # it compares like a version
           ...
   ```

   It is `None` until a frame has been read, since kvmd sends the event over
   the connection rather than in the handshake. This is the only version signal
   the socket carries; `GET /api/info` reports the full one.
2. one event per subsystem with its current state, in **no guaranteed order** —
   broadcasts meant for every client interleave with them.
3. updates from then on, whenever anything changes.

So a client that needs a particular subsystem waits for its event rather than
reading the first message — with a timeout, since a subsystem the device does
not have never sends one:

```python
import asyncio

wanted = {"atx", "hid", "streamer"}
state = {}
async with kvm.ws() as ws:
    async with asyncio.timeout(5):
        async for event in ws.events():
            state[event["event_type"]] = event["event"]
            if wanted <= state.keys():
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
| `pong` | answer to [`ping()`](#ping) on a JSON socket |

Two things a consumer has to expect:

- **Updates can be partial.** The first `streamer` event carries the whole
  state, later ones only the field that changed. `info` never sends a bundle at
  all — each event carries a single key such as `uptime` or `health`. Merge into
  what you already have instead of replacing it.
- **`clients` arrives unprompted**, broadcast to every session whenever any
  session connects or disconnects — including this one, which is why it lands
  among the initial events and again at any time afterwards.

## Typed state

`events()` hands over what arrived. `states()` hands over what it adds up to:
each event merged into what the same subsystem said before, validated against
the same model its REST endpoint returns, and yielded as one snapshot per event
that changed something.

```python
async with kvm.ws() as ws:
    async for state in ws.states():
        if state.atx:
            print("power", state.atx.leds.power)
        if state.streamer and state.streamer.streamer:
            print("fps", state.streamer.streamer.source.captured_fps)
```

A field is `None` until kvmd has sent that subsystem, which it does for all of
them when the socket opens — a device with a subsystem switched off never sends
it at all. `state.updated` is the event type behind this particular snapshot,
for a caller that would rather switch on it than re-read everything:

| Field | Model | From the event |
|---|---|---|
| `atx` | `ATXState` | `atx` |
| `gpio` | `GPIOState` | `gpio` |
| `hid` | `HIDState` | `hid` |
| `hid_keymaps` | `HIDKeymaps` | `hid_keymaps` |
| `msd` | `MSDState` | `msd` |
| `ocr` | `OCRInfo` | `ocr` |
| `streamer` | `StreamerState` | `streamer` |
| `switch` | `SwitchState` | `switch` |
| `clients` | `int` | `clients` |
| `info` | `dict` | `info` |

The merge is the point of it. kvmd sends a subsystem in full once and then only
the parts of it that change, so validating a later event on its own fails —
most of the model is simply not in it. `info` is merged the same way but stays a
raw dictionary; typing it is [#71](https://github.com/kudato/aiopikvm/issues/71).

`loop` and `pong` produce no snapshot, since neither says anything about the
device; the version the `loop` event carries is on `ws.version`. A payload that
does not match its model raises `ResponseError`, and the two iterators cannot
run over one socket at the same time — `states()` is `events()` with the states
built on top.

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

Key names are kvmd's web names — `KeyA`, `Digit1`, `ControlLeft`, `F5`; the
whole catalogue is [`KEY_NAMES`](hid.md#key-names). kvmd holds a key down
until the release arrives, and ignores a name it does not know without
answering anything at all — over this socket there is no 400 to tell a typo
from a keystroke that landed, which is why a name from an untrusted source is
worth checking against the set before it goes out.

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

Absolute positioning also needs the mouse in absolute mode. kvmd drops a
`send_mouse_move()` while the mouse is relative, and drops
`send_mouse_relative()` while it is absolute — in both cases without a word to
the sender, and with the inactivity counter bumped either way, so nothing about
the exchange says the report went nowhere. `HIDState.mouse.absolute` is which
mode is on, and `mouse.outputs.available` is what the device can switch to:

```python
state = await kvm.hid.get_state()
if state.mouse.absolute:
    await ws.send_mouse_move(0, 0)
else:
    await ws.send_mouse_relative(10, 0)
```

### Relative movement

```python
await kvm.hid.set_params(mouse_output="usb_rel")   # switches the gadget

async with kvm.ws() as ws:
    await ws.send_mouse_relative(10, 0)   # ten steps right
    await ws.send_mouse_relative(0, -10)  # ten steps up
```

Steps are in the same `-127` to `127` range as the wheel, clamped rather than
rejected, so a longer gesture is several events — which is what batching is for.

### Batching

Both relative motion and the wheel can go in one frame, which is what kvmd's own
web UI does: it collects the deltas a mouse produced between two screen
refreshes and sends them together rather than one frame per browser event.

```python
async with kvm.ws() as ws:
    await ws.send_mouse_relative_batch([(5, 0), (5, 0), (5, 2)])
    await ws.send_mouse_wheel_batch([(0, -5), (0, -5)], squash=True)
```

With `squash`, kvmd adds consecutive steps up instead of reporting each one,
starting a new sum whenever the running total would leave the `-127` to `127` a
report can carry. Fewer reports reach the host, at the cost of the shape of the
path between them. Two details worth knowing:

- A squashed batch that adds up to `(0, 0)` sends **nothing** — kvmd drops a
  final sum of zero. Without `squash`, a `(0, 0)` step is a report like any
  other.
- An empty batch is a frame kvmd does nothing with; it is not an error.

### Mouse buttons

```python
async with kvm.ws() as ws:
    # Press left button
    await ws.send_mouse_button("left", True)

    # Release left button
    await ws.send_mouse_button("left", False)
```

The names are the `MouseButton` type, shared with the REST call
([the values](error-handling.md#values-the-type-checker-catches); `up` and
`down` are the browser's back and forward buttons, not the wheel). Having a
type here is worth more than it is over HTTP: a name kvmd does not know is
dropped inside its handler with no answer of any kind, exactly as a bad key
name is.

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

Several steps can go in one frame with `send_mouse_wheel_batch()`, described
under [batching](#batching) below.

## The binary channel

kvmd accepts HID input in two encodings over the same socket. The JSON events
above are one; the other is a compact binary frame whose first byte is an
operation number — `1` key, `2` mouse button, `3` absolute move, `4` relative
move, `5` wheel, and `0` ping, which kvmd answers with `255`. Both reach the same handlers and the
same validators, and kvmd's own web UI uses the binary one for every keystroke
and mouse move, since it is a few bytes instead of a JSON object to parse.

```python
async with kvm.ws(binary=True) as ws:
    await ws.send_key("KeyA", state=True)   # b"\x01\x01KeyA"
    await ws.send_key("KeyA", state=False)  # b"\x01\x00KeyA"
```

Everything else is unchanged: the same methods, the same arguments, and events
still arrive as JSON — that direction has nothing else in it. Two details only
apply to the binary encoding:

- A key or button name goes on the wire as ASCII, and kvmd reads at most 32
  bytes of it. A name that is empty, not ASCII, or longer raises
  `ConfigurationError` instead of being sent as a frame kvmd would drop without
  a word.
- Coordinates and wheel steps are clamped before packing, since the fields they
  go into cannot hold anything else. kvmd clamps the JSON ones the same way, so
  the device ends up with the same values either way.

It is off by default because JSON is what this client has always sent, and it
is the encoding a packet capture can be read in. The binary channel is verified
against kvmd 4.186 — every frame in the `ws_binary` fixture was sent to that
device and accepted by it.

Whichever encoding you use, kvmd drops what it cannot decode without telling
the client: a key name its validator refuses, a frame too short to unpack, an
operation it has no handler for. It writes a line to its own log — `Unknown
websocket binary event: b'\xc8'` for an operation, `Unknown websocket event`
for a JSON one — and the sender hears nothing either way. Nothing about the
input path is acknowledged, so a caller that needs to know an event landed has
to look at the device: `kvm.hid.get_inactivity()` returns to 0 for every event
kvmd accepted, and keeps counting for one it dropped.

## Ping

```python
async with kvm.ws() as ws:
    latency = await ws.ping()       # round trip in seconds
```

This is kvmd's application-level ping, and it waits for the answer. The request
goes through the same event loop that dispatches HID input and broadcasts
state, so a pong means that loop is running — not merely that something on the
other end still holds a TCP socket open. `WebSocketError` is raised if the
answer does not arrive within `timeout` (10 seconds by default), or if the
connection breaks or closes first.

The answer arrives on the socket like everything else, so `ping()` waits for
whoever is reading it. Both arrangements work:

```python
# Nobody else reading: ping() reads the socket itself, and keeps the events it
# finds on the way for the next events() call.
async with kvm.ws() as ws:
    if await ws.ping() > 0.5:
        print("the device is struggling")

# Reading in another task: that iteration hands the pong over.
async def watch(ws):
    async for event in ws.events():
        handle(event)

async with kvm.ws() as ws:
    reader = asyncio.create_task(watch(ws))
    while True:
        await asyncio.sleep(5)
        print(await ws.ping())
```

The round trip is measured from the frame going out to the pong being read, so
a consumer of `events()` that takes its time between frames adds its own delay
to the number — the pong waits behind whatever it is doing.

On a JSON socket the answer is also a `pong` event, and `events()` yields it
like any other. On a binary one it is operation `255`, which is not an event and
does not appear there.

Keeping the socket alive needs none of this. The underlying library sends a
protocol-level ping every 20 seconds and closes the connection if one goes
unanswered for another 20, which is how a link that dies without a close frame
surfaces as `WebSocketError` from `events()` rather than hanging forever.

## Standalone usage

`PiKVMWebSocket` can also be used independently:

```python
from aiopikvm import PiKVMWebSocket

ws = PiKVMWebSocket(
    url="https://pikvm.local",
    user="admin",
    passwd="admin",
    verify_ssl=False,     # or a CA bundle path, or an ssl.SSLContext
    proxy=None,           # or "http://proxy.local:3128"
    trust_env=True,       # read WSS_PROXY / HTTPS_PROXY / NO_PROXY
    stream=True,
    binary=False,
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
