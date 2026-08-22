# Live Video

[Streamer & OCR](streamer.md) covers one frame at a time. This page covers the
moving picture: ustreamer's MJPEG stream, ustreamer's own state endpoint, and
the H.264 stream the `kvmd-media` daemon serves over a WebSocket.

Two things are true of everything here:

- **The streamer only runs while somebody asks for video.** kvmd counts the
  connected sessions that opened with `stream=1` and shuts the streamer process
  down when that count reaches zero, so a reader has to hold a session open of
  its own. See [Keeping the streamer up](#keeping-the-streamer-up).
- **Nothing on these paths speaks the kvmd envelope.** `/streamer/*` is
  ustreamer behind nginx and the media socket is a separate daemon, so a
  failure has no `error` field to match on and a stopped streamer arrives as an
  nginx **HTTP 502** rather than the `UnavailableError` (HTTP 503) the REST API
  answers with.

## Keeping the streamer up

Open a [WebSocket](websocket.md) with `stream=True` (the default) and hold it
for as long as video is wanted:

```python
async with kvm.ws():
    ...  # read video here
```

That is all of it — the socket reads itself, and a session that never touches
`events()` keeps counting as a viewer for as long as the block runs. See
[Backpressure](websocket.md#backpressure) for why that has to be true and what
happens to the events nobody collects.

Devices configured with `kvmd.streamer.forever: true` run the streamer
regardless, and none of this applies to them.

## ustreamer's own state

`kvm.streamer.get_state()` asks kvmd, which polls ustreamer and relays the
answer. `kvm.streamer.get_ustreamer_state()` asks ustreamer directly and
returns the same [`Streamer`][aiopikvm.Streamer] model:

```python
state = await kvm.streamer.get_ustreamer_state()
print(state.source.resolution.width, state.encoder.type)
print(state.stream.clients, "clients")
```

The difference is freshness. kvmd's copy is as old as its last poll; this one
is current, which is what makes the per-client counters usable for watching a
stream this process opened itself:

```python
for stat in state.stream.clients_stat.values():
    print(stat.key or "<unnamed>", stat.fps, "fps")
```

With the streamer stopped there is no upstream socket for nginx to reach, so
this raises `APIError` with `status_code=502` — not `UnavailableError`.

## MJPEG

`mjpeg()` reads ustreamer's `multipart/x-mixed-replace` stream, the one a
browser renders by pointing an `<img>` at it, and yields one
[`MJPEGFrame`][aiopikvm.MJPEGFrame] per part:

```python
async for frame in kvm.streamer.mjpeg():
    print(len(frame.data), "bytes at", frame.timestamp)
    break
```

The iteration ends only when the far end stops sending, so it is a loop to
leave with a `break` or to cancel from outside. The read timeout is disabled by
default — a stream has no end to wait for — while connect, write and pool keep
their client-level values.

### Naming a connection

Only `frame.timestamp` is filled in by default. `extra_headers=True` asks
ustreamer to annotate every part, and `key=...` names the connection so its own
row can be found in `clients_stat` — the id those are keyed by is assigned by
ustreamer and never sent to the client it belongs to:

```python
async for frame in kvm.streamer.mjpeg(key="recorder", extra_headers=True):
    print(f"{frame.width}x{frame.height}, online={frame.online}, "
          f"dropped={frame.dropped}, {frame.client_fps} fps")

    stats = (await kvm.streamer.get_ustreamer_state()).stream.clients_stat
    mine = next(s for s in stats.values() if s.key == "recorder")
    print("ustreamer agrees:", mine.fps, "fps")
    break
```

The row exists only while the connection does, so read it from inside the loop.
Anything the part headers carried is on `frame.headers` verbatim, including
whatever a newer ustreamer adds.

### Timings without the pictures

`zero_data=True` asks for the part headers with no JPEG behind them, which
turns the stream into a cheap frame-timing feed — `frame.data` is then empty:

```python
async for frame in kvm.streamer.mjpeg(extra_headers=True, zero_data=True):
    print(frame.timestamp, frame.latency, frame.dropped)
```

!!! note "Two ustreamer flags are deliberately missing"
    `advance_headers` sends each part's headers before the frame they describe
    exists, which drops `Content-Length` — and every `X-UStreamer-*` header
    with it — so no parser that finds frames by their declared length can
    follow it. It is a Chromium rendering workaround, and `dual_final_frames`
    is the same for Safari. Neither has anything to offer a client that reads
    bytes, so neither is offered.

## H.264 over the media socket

`kvmd-media` is a separate daemon with a REST endpoint that says what it can
send and a WebSocket that sends it.

```python
media = await kvm.media.get_state()
if media.video.h264 is not None:
    print("H.264, profile-level-id", media.video.h264.profile_level_id)
if media.video.jpeg is not None:
    print("MJPEG too")
```

A format the daemon does not serve is simply absent, and asking the socket for
one that is not there is refused with `APIError` (HTTP 400,
`error="ValidatorError"`) before the socket exists.

### The short way

`kvm.media_ws()` opens a socket that streams a single format and nothing else.
Frames start arriving during the handshake:

```python
async with kvm.media_ws() as ws:          # video="h264" by default
    async for frame in ws.frames():
        assert frame.data[:4] == b"\x00\x00\x00\x01"   # Annex B start code
        handle(frame.data)
```

Every message is one raw frame, so `frame.key` is `None` — the flag lives in a
message type this mode does not use. `request_keyframe()` asks the encoder for
one, which is how a consumer that joined mid-stream gets something decodable:

```python
await ws.request_keyframe()
```

### The long way

`video=None` opens the daemon's general-purpose socket instead. It announces
what it can send, and sends nothing until asked:

```python
async with kvm.media_ws(video=None) as ws:
    print(ws.media.video.h264.profile_level_id)   # the same as GET /api/media
    await ws.start(media_format="h264")
    async for frame in ws.frames():
        if frame.key:
            print("keyframe")
        handle(frame.data)
```

`ws.media` is the announcement, already parsed as a
[`MediaState`][aiopikvm.MediaState] and available before the first frame; on a
single-format socket it is `None`, and `ws.pure` tells the two apart. Frames
here carry `frame.key`, and this socket also answers `ping()`:

```python
await ws.ping()    # fire-and-forget; the reply arrives among the frames
```

`start()` and `ping()` raise `ConfigurationError` on a single-format socket,
which has no use for either.

### Backpressure

The media socket has the same trap as the event socket, from the other side: a
consumer slower than the encoder fills the inbound queue, the library stops
reading the transport, and the connection dies on its own keepalive. The
defaults are chosen for video — a deeper queue than the event socket's, and no
size cap, since a keyframe of a 1080p screen is comfortably over a megabyte:

```python
async with kvm.media_ws(
    max_queue=256,        # frames buffered before the read pauses
    max_size=None,        # no per-message cap (the default)
    ping_interval=20.0,
    ping_timeout=20.0,
) as ws:
    ...
```

Hand frames off to something that cannot block — a queue, a file, a subprocess
— rather than decoding them inside the `frames()` loop.

## Full example

Recording ten seconds of H.264 to a file, with a session held open throughout:

```python
import asyncio
import contextlib
from aiopikvm import PiKVM

async def main() -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        async with kvm.ws():                    # keeps the streamer running
            with open("capture.h264", "wb") as out:
                async with kvm.media_ws() as video:
                    await video.request_keyframe()
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(10):
                            async for frame in video.frames():
                                out.write(frame.data)

asyncio.run(main())
```

The result is a raw Annex B elementary stream: `ffmpeg -i capture.h264 out.mp4`
puts a container around it.
