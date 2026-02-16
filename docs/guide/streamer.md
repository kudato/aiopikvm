# Streamer & OCR

The Streamer resource captures screenshots and performs OCR (optical character recognition) on the current screen.

## Get state

```python
state = await kvm.streamer.get_state()
print(f"Enabled: {state.enabled}")
print(f"Source online: {state.source.online}")
if state.source.resolution:
    print(f"Resolution: {state.source.resolution.width}x{state.source.resolution.height}")
```

## Take a screenshot

Returns raw JPEG image bytes:

```python
jpeg_bytes = await kvm.streamer.snapshot()

# Save to file
with open("screenshot.jpeg", "wb") as f:
    f.write(jpeg_bytes)
```

## OCR

Read text from the current screen:

```python
text = await kvm.streamer.ocr()
print(text)
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
        # Check if source is online
        state = await kvm.streamer.get_state()
        if not state.source.online:
            print("Video source is offline")
            return

        # Take a screenshot
        jpeg = await kvm.streamer.snapshot()
        with open("screen.jpeg", "wb") as f:
            f.write(jpeg)

        # Read text from screen
        text = await kvm.streamer.ocr()
        if "login" in text.lower():
            print("Login screen detected")

asyncio.run(main())
```
