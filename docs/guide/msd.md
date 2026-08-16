# Mass Storage (MSD)

The MSD resource manages virtual mass storage drives — upload disk images, connect/disconnect drives, and configure drive parameters.

## Get state

```python
state = await kvm.msd.get_state()
print(f"Enabled: {state.enabled}")
print(f"Online: {state.online}")
print(f"Busy: {state.busy}")

if state.drive is not None and state.storage is not None:
    print(f"Drive connected: {state.drive.connected}")
    print(f"CD-ROM mode: {state.drive.cdrom}")
    print(f"Image in the drive: {state.drive.image.name if state.drive.image else None}")
    print(f"Stored images: {', '.join(state.storage.images)}")
    print(f"Free space: {state.storage.parts[''].free}")
```

`drive` and `storage` are both `None` while the subsystem is offline — the
MSD is disabled in the OTG profile, or kvmd has not finished setting it up.
Neither is ever available without the other, so one `if` covers both.

Free space is reported per partition rather than for the storage as a whole;
the root partition is keyed by the empty string.

## Upload images

### From bytes

```python
with open("image.iso", "rb") as f:
    data = f.read()

await kvm.msd.upload("image.iso", data)
```

### From async iterator

For large files, use an async iterator to avoid loading the entire file into memory.
kvmd takes the image size from `Content-Length`, so a streamed upload has to declare
it up front — pass `size`:

```python
import os

import aiofiles

async def read_chunks(path: str, chunk_size: int = 65536):
    async with aiofiles.open(path, "rb") as f:
        while chunk := await f.read(chunk_size):
            yield chunk

path = "/path/to/image.iso"
await kvm.msd.upload(
    "large-image.iso",
    read_chunks(path),
    size=os.path.getsize(path),
    timeout=3600,
)
```

Pass `remove_incomplete=True` to have kvmd delete a partially written image if the
connection breaks; otherwise the incomplete image stays in storage with
`complete=False`.

### From remote URL

```python
await kvm.msd.upload_remote("https://example.com/image.iso")

# With custom timeout
await kvm.msd.upload_remote("https://example.com/image.iso", timeout=300)
```

## Select or eject an image

```python
# Put a stored image into the drive
await kvm.msd.set_params(image="boot.iso")

# Eject it
await kvm.msd.set_params(image="")
```

The name has to be one of `state.storage.images`. kvmd rebuilds that listing
from the storage partition, so an image is not selectable for a moment right
after it finishes uploading.

## Download an image

```python
with open("copy.iso", "wb") as f:
    async for chunk in kvm.msd.download("boot.iso"):
        f.write(chunk)

# Compressed on the fly; the response then has no Content-Length
async for chunk in kvm.msd.download("boot.iso", compress="zstd"):
    ...
```

## Drive parameters

```python
# Set CD-ROM mode
await kvm.msd.set_params(cdrom=True)

# Set read-write mode
await kvm.msd.set_params(rw=True)

# Set both
await kvm.msd.set_params(cdrom=False, rw=True)
```

## Connect / disconnect

```python
# Connect the drive to the host
await kvm.msd.set_connected(True)

# Disconnect
await kvm.msd.set_connected(False)
```

## Remove images

```python
await kvm.msd.remove("old-image.iso")
```

## Reset

```python
await kvm.msd.reset()
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        # Upload an ISO and connect as CD-ROM
        with open("boot.iso", "rb") as f:
            await kvm.msd.upload("boot.iso", f.read())

        await kvm.msd.set_params(cdrom=True)
        await kvm.msd.set_connected(True)

        # Reboot the host to boot from the virtual CD
        await kvm.atx.click_reset()

asyncio.run(main())
```
