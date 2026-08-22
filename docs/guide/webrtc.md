# WebRTC Video

PiKVM serves the same picture three ways, and this is the third: the Janus
gateway at `/janus/ws`, with ustreamer's own plugin loaded into it. It is what
the web UI opens by default, because it is the one with the lowest latency —
which matters to a person moving a mouse and not much to a program reading the
screen. [Live Video](video.md) covers the other two.

It is also the only part of aiopikvm that needs something extra:

```bash
pip install 'aiopikvm[webrtc]'
```

That pulls [aiortc](https://github.com/aiortc/aiortc), and with it a bundled
FFmpeg, a DTLS stack and an SRTP binding — several times the size of
everything the rest of the client needs. Nothing else imports it, and this
module does not touch it until a session is actually opened, so an install
without the extra behaves exactly as before.

## Which path to take

| | Transport | Frames arrive | Extra install |
|---|---|---|---|
| [`webrtc()`][aiopikvm.PiKVM.webrtc] | WebRTC over UDP | decoded | aiortc |
| [`media_ws()`][aiopikvm.PiKVM.media_ws] | WebSocket | encoded H.264 or JPEG | none |
| [`streamer`](streamer.md) | HTTP | MJPEG parts | none |

Recording, frame processing and embedding a stream elsewhere are all better
served by the middle row: the bytes arrive encoded, a decoder is only needed
if you actually want pixels, and nothing has to negotiate. Reach for WebRTC
when the latency is the point.

## Opening a session

```python
import asyncio
from aiopikvm import PiKVM

async def main() -> None:
    async with PiKVM("https://pikvm.local", passwd="secret") as kvm:
        async with kvm.ws():                # keeps the streamer running
            async with kvm.webrtc() as rtc:
                print(rtc.features)
                async for frame in rtc.video():
                    image = frame.to_ndarray(format="bgr24")
                    print(image.shape)
                    break

asyncio.run(main())
```

Entering the block does the whole negotiation and returns once Janus reports
the peer connection up, so a block that starts running has video on the way.
Leaving it stops the stream and destroys the session, whatever happened
inside.

The frames are PyAV `VideoFrame` objects — already through the H.264 decoder,
since aiortc has to decode to depacketize — so `to_ndarray()` and `to_image()`
are one call away. That is the difference from
[`media_ws()`][aiopikvm.PiKVM.media_ws], which hands over the encoded stream
and leaves the decoding to you.

!!! warning "A `ws()` has to be open beside it"
    kvmd runs ustreamer only while some session has asked to be counted as a
    viewer — that is what [`ws()`][aiopikvm.PiKVM.ws] does by default — and
    the Janus plugin reads its frames out of ustreamer. Open a WebRTC session
    on its own and the whole negotiation succeeds, Janus reports the peer
    connection up, and then nothing arrives: no frames, no error, no event
    saying why. This is the same requirement
    [`media_ws()`][aiopikvm.PiKVM.media_ws] has, and it is worth stating
    plainly because here the silence is the only symptom.

## What the negotiation looks like

Janus has its own protocol, and none of it is the kvmd `{"ok": …, "result":
…}` envelope. Nine messages go over the signalling socket before a frame
arrives:

| | Message | What comes back |
|---|---|---|
| 1 | `create` | the session id |
| 2 | `attach` `janus.plugin.ustreamer` | the handle id |
| 3 | `features` | what the device can do |
| 4 | `watch` | **an SDP offer** |
| 5 | `start` with the answer | `started` |
| 6 | `trickle` `{"completed": true}` | an acknowledgement |
| 7 | — | `webrtcup` |
| 8 | `stop`, `detach` | `stopped` |
| 9 | `destroy` | the session is gone |

Two things about that are worth knowing, because they are backwards from the
usual and they explain the shape of everything below.

**The plugin offers.** In most WebRTC code the client creates the offer and
the server answers. Here `watch` comes back carrying the offer, and the
client owes the answer, which it sends inside `start`.

**The plugin pushes.** A request is answered twice: Janus acknowledges the
message straight away, and the plugin's own answer arrives afterwards as a
separate event rather than as a reply. So a plugin error is not a Janus error
— it rides inside a message Janus considers perfectly successful, and it has
its own numbering: 400 for a malformed body, 405 for a request the plugin does
not implement. [`WebRTCError`][aiopikvm.WebRTCError] carries whichever code it
was.

The session sends a `keepalive` every 25 seconds on its own, because Janus
drops a session that has been silent for 60 and takes the peer connection with
it.

## ICE, and why there is none by default

`ice_servers` is empty unless you fill it in. A PiKVM sits on the same network
as whatever is talking to it, host candidates reach it, and a STUN server is a
third party this client will not contact without being asked.

The device does suggest one — whatever `JANUS_USTREAMER_WEB_ICE_URL` was set
to, or the plugin's compiled-in default, which is usually a public Google STUN
server. It arrives on [`features`][aiopikvm.WebRTCSession.features] and is
never used unless you pass it back in:

```python
async with kvm.webrtc() as rtc:
    print(rtc.features.ice.url)   # what the device suggests

# Only if you actually need it — a client behind NAT from the device.
async with kvm.webrtc(ice_servers=["stun:stun.example.org:3478"]) as rtc:
    ...
```

## Backpressure

aiortc queues decoded frames without a limit, so a consumer that falls behind
turns into memory that grows. This client puts a bound on it: the newest
`frame_buffer` frames per track are kept and the oldest goes when a new one
arrives.

That is the right trade for live video — the newest frame is the only one
worth having — but it does mean a slow loop silently skips frames. If you need
every frame, you are recording rather than watching, and
[`media_ws()`][aiopikvm.PiKVM.media_ws] is the better tool: it hands over the
encoded stream and never decodes anything you were going to throw away.

```python
async with kvm.webrtc(frame_buffer=64) as rtc:  # more slack, more latency
    ...
```

## Audio

`audio=True` asks for the host's sound alongside the video, and
[`audio()`][aiopikvm.WebRTCSession.audio] yields the decoded frames. The
device needs a capture device for it — [`features.audio`][aiopikvm.WebRTCFeatures]
says whether it has one — and asking for it on a device without one simply
yields nothing.

```python
async with kvm.webrtc(audio=True) as rtc:
    if not rtc.features.audio:
        print("this device has no audio capture")
```

Sending audio *to* the host — the plugin's `mic` — is not implemented: it
needs a source track from the caller, which is a different shape of API.

## Watching the session

[`events()`][aiopikvm.WebRTCSession.events] hands over what Janus says about
the session while it runs: `webrtcup` when the DTLS handshake finished,
`slowlink` when the link is congested, `hangup` when the peer connection
ended, `timeout` when Janus gave up on the session. None of it has to be read
— the buffer drops its oldest and ignoring it costs nothing — but it is where
"the picture froze" gets a reason.

```python
async def watch(rtc):
    async for event in rtc.events():
        if event.janus == "hangup":
            print("gone:", event.reason)
        elif event.janus == "slowlink":
            print("congested, lost", event.lost)
```

Do not wait for `media`. Janus events it for the media it *receives*, and a
session that only watches sends none, so it never comes — on the recorded
session `webrtcup` is the only thing that arrives, and the first frame is the
only evidence that video started. That is also why a missing viewer socket is
so quiet: nothing in the signalling reports it.

## Keyframes

[`request_keyframe()`][aiopikvm.WebRTCSession.request_keyframe] asks the
encoder for one now. Useful after a decoder has lost its reference, or to
shorten the wait when joining a stream whose group of pictures is long. The
plugin sets a flag and says nothing back, so there is no acknowledgement to
await — the keyframe just turns up.

## Errors

Everything under `/janus/ws` reports through
[`WebRTCError`][aiopikvm.WebRTCError], which carries Janus's own numbering
rather than an HTTP status:

```python
from aiopikvm import WebRTCError

try:
    async with kvm.webrtc() as rtc:
        ...
except WebRTCError as exc:
    print(exc.code, exc.reason)
```

The upgrade itself is different: kvmd's auth sits in front of Janus, so bad
credentials are refused before Janus ever sees the request and arrive as the
ordinary [`AuthError`][aiopikvm.AuthError] every other call would raise. A
missing extra is a [`ConfigurationError`][aiopikvm.ConfigurationError], raised
before anything reaches the device.

## Reaching the track directly

[`track()`][aiopikvm.WebRTCSession.track] hands over the aiortc
`MediaStreamTrack`, for feeding aiortc's own `MediaRecorder` or `MediaRelay`.
The session is already pulling frames off it into its own buffer, so use one
or the other rather than both.
