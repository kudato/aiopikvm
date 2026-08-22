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

kvmd builds the response out of eight submanagers: `auth`, `extras`, `fan`,
`health`, `meta`, `node`, `system` and `uptime`.

### The legacy shape

A ninth name, `hw`, is not a submanager. It belongs to the shape kvmd's older
API had, which is still the default, and asking for it puts kvmd through a
rearrangement worth knowing about:

| | Legacy (default) | `legacy=False` |
|---|---|---|
| `hw` | present, holding `health` and `platform` | refused — HTTP 400 |
| `health` at top level | **not** in the default set | in the default set |
| `system.platform` | moved into `hw` whenever `hw` was asked for | stays in `system` |
| `system` | dropped unless named, even though `hw` needs it | returned when asked for |

So a call that names no field does **not** return every category — `health` is
missing from it — and `get_info("hw")` comes back with `hw` alone, because kvmd
fetched `system` to build `hw` and then discarded it.

```python
# The modern per-submanager shape, the same one the WebSocket info events use
info = await kvm.system.get_info(legacy=False)
print(info["health"]["temp"])
print(info["system"]["platform"]["model"])
```

`legacy=True` is kvmd's own default, so a plain call sends no `legacy` param at
all and the request is unchanged from what earlier versions of this client sent.

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
