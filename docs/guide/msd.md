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

Every upload answers with what kvmd wrote — the name it stored the image
under, the total it was opened for, and how much landed. Read the name back
rather than assuming it: kvmd runs it through its own file-name validator and
joins any `prefix` onto it, and that stored name is what `set_params()` and
`remove()` take.

### From bytes

```python
with open("image.iso", "rb") as f:
    data = f.read()

info = await kvm.msd.upload("image.iso", data)
print(f"stored as {info.name}, {info.written}/{info.size} bytes")
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
`complete=False` — and occupying the name, so the retry is refused as already
existing.

`prefix` writes into a subdirectory of the storage, but only one that already
exists. kvmd creates the image's `.incomplete` marker before it creates the
directory, so a prefix that is not there yet fails on an unhandled
`FileNotFoundError`: a plain-text HTTP 500 with no error block, which arrives
as an `APIError` carrying only the status.

### From remote URL

kvmd downloads the image itself; it never passes through this client.

```python
info = await kvm.msd.upload_remote(
    "https://example.com/image.iso",
    name="boot.iso",           # else kvmd names it after the remote
    remove_incomplete=True,
)
print(f"stored as {info.name}")
```

The endpoint streams NDJSON while it works, so progress is available without
polling `get_state()`:

```python
async for progress in kvm.msd.upload_remote_progress(
    "https://example.com/image.iso", remove_incomplete=True
):
    print(f"{progress.written} / {progress.size} bytes")
```

Iterating to the end is what waits for the download. Stopping early closes the
connection, and kvmd gives up on the transfer as soon as the next record it
writes finds it gone — do it through `contextlib.aclosing()` so that happens
when you decide rather than whenever the generator is collected.

Both calls raise `APIError` for a download kvmd could not finish, but note
where that failure comes from: kvmd has already answered HTTP 200 by the time
it knows, so it reports the reason as the last record of the stream rather
than as a status. A refusal it can make up front — an unusable URL, an origin
that answers anything but 200 or sends no `Content-Length`, a name already in
storage — is an ordinary 400.

`connect_timeout` is kvmd's own `timeout` parameter: how long *it* waits to
connect to the URL, defaulting to 10 s. It does not bound the download — kvmd
puts no limit on the total and allows a week between chunks. Use `timeout` for
this client's side of the request, whose read timeout is disabled by default
because the response stays open for the whole transfer.

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

The name is the one in `state.storage.images`, subdirectory included. The file
is gone when the call returns, but the listing kvmd checks a write against is
rebuilt a moment later — so uploading the same name straight afterwards is
refused as already existing. Poll `get_state()` until `storage.images` has
dropped it.

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
            info = await kvm.msd.upload("boot.iso", f.read(), timeout=3600)

        # Uploading does not select the image; without this step
        # set_connected() fails with "The image is not selected". Select it
        # by the name kvmd stored, not the one that was uploaded.
        await kvm.msd.set_params(image=info.name, cdrom=True)
        await kvm.msd.set_connected(True)

        # Reboot the host to boot from the virtual CD
        await kvm.atx.click_reset()

asyncio.run(main())
```
