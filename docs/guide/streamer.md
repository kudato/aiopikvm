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
    print(f"Online at {res.width}x{res.height}, {state.streamer.h264.fps} fps")
```

## Take a screenshot

Returns raw JPEG image bytes:

```python
jpeg_bytes = await kvm.streamer.snapshot()

with open("screenshot.jpeg", "wb") as f:
    f.write(jpeg_bytes)
```

By default `snapshot()` returns HTTP 503 if the video source is offline.
Pass `allow_offline=True` to receive a "NO LIVE VIDEO" placeholder
instead:

```python
jpeg_bytes = await kvm.streamer.snapshot(allow_offline=True)
```

The flag has no effect when the streamer process is fully stopped — that
case still raises `APIError(503)`.

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

        jpeg = await kvm.streamer.snapshot()
        with open("screen.jpeg", "wb") as f:
            f.write(jpeg)

        text = await kvm.streamer.ocr()
        if "login" in text.lower():
            print("Login screen detected")

asyncio.run(main())
```
