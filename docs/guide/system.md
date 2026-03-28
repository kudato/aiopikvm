# System Info & Logs

The System resource provides device information and KVMD service logs.

## Get device info

```python
info = await kvm.system.get_info()
print(info["hw"]["platform"]["type"])  # e.g. "rpi"
print(info["system"]["kvmd"]["version"])
```

### Filter by category

```python
# Only hardware info
info = await kvm.system.get_info("hw")

# Multiple categories
info = await kvm.system.get_info("hw", "system")
```

Available categories: `auth`, `extras`, `fan`, `hw`, `meta`, `system`.

## Get logs

Fetch KVMD service logs as plain text:

```python
log = await kvm.system.get_log()
print(log)
```

### With history

```python
# Get last hour of logs
log = await kvm.system.get_log(seek=3600)
```

## Stream logs

Stream logs in real time using `follow=1` mode. The connection stays open and yields new lines as they arrive:

```python
async for line in kvm.system.stream_log():
    print(line)
```

### With history

```python
# Stream with last hour of history first
async for line in kvm.system.stream_log(seek=3600):
    print(line)
```

!!! note
    `stream_log()` disables the read timeout to support long-lived connections. The connect timeout still applies.

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        # Device info
        info = await kvm.system.get_info("hw", "system")
        hw = info["hw"]
        print(f"Platform: {hw['platform']['base']}")
        print(f"KVMD: {info['system']['kvmd']['version']}")

        # Stream logs for 10 lines
        count = 0
        async for line in kvm.system.stream_log():
            print(line)
            count += 1
            if count >= 10:
                break

asyncio.run(main())
```
