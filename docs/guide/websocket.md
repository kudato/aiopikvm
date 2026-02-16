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
    stream=0,              # Stream index (default: 0)
    open_timeout=10.0,     # Connection timeout
    close_timeout=10.0,    # Close timeout
) as ws:
    ...
```

## Receiving events

Iterate over incoming events using `events()`:

```python
async with kvm.ws() as ws:
    async for event in ws.events():
        print(event)
```

Events are dictionaries parsed from JSON messages. The first message after connection is a full state bundle, followed by incremental updates.

## Keyboard input

```python
async with kvm.ws() as ws:
    # Press a key
    await ws.send_key("KeyA", state=True)

    # Release a key
    await ws.send_key("KeyA", state=False)
```

## Mouse input

### Move mouse

```python
async with kvm.ws() as ws:
    await ws.send_mouse_move(500, 300)
```

### Mouse buttons

```python
async with kvm.ws() as ws:
    # Press left button
    await ws.send_mouse_button("left", True)

    # Release left button
    await ws.send_mouse_button("left", False)
```

### Mouse wheel

```python
async with kvm.ws() as ws:
    # Scroll down
    await ws.send_mouse_wheel(0, 5)
```

## Ping

Keep the connection alive:

```python
async with kvm.ws() as ws:
    await ws.ping()
```

## Standalone usage

`PiKVMWebSocket` can also be used independently:

```python
from aiopikvm import PiKVMWebSocket

ws = PiKVMWebSocket(
    url="https://pikvm.local",
    user="admin",
    passwd="admin",
    verify_ssl=False,
    stream=0,
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
