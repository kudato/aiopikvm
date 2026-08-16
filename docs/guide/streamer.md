# Streamer & OCR

The Streamer resource captures screenshots and performs OCR (optical character recognition) on the current screen.

## Get state

`StreamerState.streamer` is `None` when no clients are subscribed to the
stream — kvmd shuts the streamer process down to save resources. When it
is running, `streamer.source.online` indicates whether a video signal is
present (host awake, HDMI plugged).

```python
state = await kvm.streamer.get_state()
if state.streamer is None:
    print("Streamer is not running (no active stream clients)")
elif not state.streamer.source.online:
    print("Streamer running, but no video signal")
else:
    res = state.streamer.source.resolution
    print(f"Online at {res.width}x{res.height}")
    if state.streamer.h264 is not None:
        print(f"H.264 at {state.streamer.h264.fps} fps")
```

What the device supports decides which parameters exist at all: `quality`
only where the encoder is adjustable, `h264_bitrate`/`h264_gop` only where
H.264 is configured, `resolution` and `limits.available_resolutions` only on
capture hardware that can switch. `features` says which of the three the
device has, and the corresponding fields are `None` when it does not.

`params` is what was requested and `applied` is what the running streamer
ended up with — comparing them is the only way to see whether a change took
effect:

```python
if state.applied.desired_fps != state.params.desired_fps:
    print("The requested rate has not been applied")
```

## Change parameters

```python
await kvm.streamer.set_params(quality=70, desired_fps=25)

# Only on resolution-capable hardware
await kvm.streamer.set_params(resolution="1280x720")
```

Asking for a parameter the device does not have at all fails with `APIError`
(HTTP 400). A value outside `state.limits` does *not*: kvmd answers 200 and
then drops it. Since the change is applied asynchronously either way, reading
`applied` back is the only way to know what happened.

## Restart the streamer

The usual recovery for a frozen pipeline. Video drops for a moment:

```python
await kvm.streamer.reset()
```

## Take a screenshot

```python
image = await kvm.streamer.snapshot()

with open("screenshot.jpeg", "wb") as f:
    f.write(image.data)

print(f"{image.width}x{image.height}, live: {image.online}")
```

By default `snapshot()` returns HTTP 503 if the video source is offline.
Pass `allow_offline=True` to receive a "NO LIVE VIDEO" placeholder
instead — `image.online` then tells the two apart, which the bytes alone
cannot:

```python
image = await kvm.streamer.snapshot(allow_offline=True)
if not image.online:
    print("That is the placeholder, not the host screen")
```

The flag has no effect when the streamer process is fully stopped — that
case still raises `UnavailableError` (HTTP 503).

### Saved snapshots

`save=True` stores the frame on the device, where it shows up in
`state.snapshot` and outlives the streamer being stopped. `load=True` returns
that stored frame and works even then, which is the point of it:

```python
await kvm.streamer.snapshot(save=True)

# Later, with no stream clients and the streamer shut down:
image = await kvm.streamer.snapshot(load=True)
```

`load=True` with nothing saved raises `UnavailableError`; `state.snapshot.saved`
says whether there is one, and how big it is.

### Previews

kvmd can scale the frame down before sending it, which is much cheaper than
fetching a full-resolution JPEG just to thumbnail it:

```python
image = await kvm.streamer.snapshot(
    preview=True, preview_max_width=640, preview_quality=50
)
```

Omitting both bounds gives a fifth of the source size.

## OCR

Read text from the current screen:

```python
text = await kvm.streamer.ocr()
print(text)

# Multi-language recognition
text = await kvm.streamer.ocr(langs=["eng", "rus"])

# Read text from the placeholder when the source is offline
text = await kvm.streamer.ocr(allow_offline=True)
```

Cropping is what makes OCR usable in a loop — Tesseract needs 10-20 seconds
for a full screen, but a fraction of that for a region:

```python
text = await kvm.streamer.ocr(left=100, top=50, right=800, bottom=200)
```

`ocr()` uses a 30 s default timeout because Tesseract on the Pi is slow
(10–20 s for full-screen recognition). Override via the `timeout`
argument if needed.

To inspect installed OCR languages:

```python
info = await kvm.streamer.get_ocr_info()
print(info.langs.available)  # e.g. ["eng", "osd", "rus"]
print(info.langs.default)    # e.g. ["eng"]
```

!!! note
    OCR must be enabled in the PiKVM configuration. The quality depends on the screen resolution and font rendering.

## Delete cached snapshot

```python
await kvm.streamer.delete_snapshot()
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        state = await kvm.streamer.get_state()
        if state.streamer is None or not state.streamer.source.online:
            print("Video source is offline")
            return

        image = await kvm.streamer.snapshot()
        with open("screen.jpeg", "wb") as f:
            f.write(image.data)

        text = await kvm.streamer.ocr()
        if "login" in text.lower():
            print("Login screen detected")

asyncio.run(main())
```
