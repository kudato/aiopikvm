# Mass Storage (MSD)

The MSD resource manages virtual mass storage drives — upload disk images, connect/disconnect drives, and configure drive parameters.

## Get state

```python
state = await kvm.msd.get_state()
print(f"Enabled: {state.enabled}")
print(f"Online: {state.online}")
print(f"Busy: {state.busy}")
print(f"Drive image: {state.drive.image}")
print(f"Drive connected: {state.drive.connected}")
print(f"CD-ROM mode: {state.drive.cdrom}")
print(f"Storage size: {state.storage.size}")
print(f"Storage free: {state.storage.free}")
```

## Upload images

### From bytes

```python
with open("image.iso", "rb") as f:
    data = f.read()

await kvm.msd.upload("image.iso", data)
```

### From async iterator

For large files, use an async iterator to avoid loading the entire file into memory:

```python
import aiofiles

async def read_chunks(path: str, chunk_size: int = 65536):
    async with aiofiles.open(path, "rb") as f:
        while chunk := await f.read(chunk_size):
            yield chunk

await kvm.msd.upload("large-image.iso", read_chunks("/path/to/image.iso"))
```

### From remote URL

```python
await kvm.msd.upload_remote("https://example.com/image.iso")

# With custom timeout
await kvm.msd.upload_remote("https://example.com/image.iso", timeout=300)
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
