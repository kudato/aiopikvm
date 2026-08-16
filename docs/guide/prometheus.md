# Prometheus Metrics

kvmd ships a small exporter at `/api/export/prometheus/metrics`.
`get_metrics()` returns its output verbatim, as a string.

```python
metrics = await kvm.prometheus.get_metrics()
print(metrics)
```

## What the output looks like

Every metric is a gauge, and each one is preceded by a single `# TYPE` line:

```text
# TYPE pikvm_atx_enabled gauge
pikvm_atx_enabled 0

# TYPE pikvm_atx_power gauge
pikvm_atx_power 0

# TYPE pikvm_hw_cpu_percent gauge
pikvm_hw_cpu_percent 47

# TYPE pikvm_hw_temp_cpu gauge
pikvm_hw_temp_cpu 38.459
```

!!! note
    There are **no `# HELP` lines** — kvmd does not emit them. Anything that
    parses the output must not require a description per metric.

## What is covered

The exporter reads four subsystems and nothing else:

| Prefix | Source | Metrics |
| --- | --- | --- |
| `pikvm_atx_*` | ATX | exactly two: `pikvm_atx_enabled` and `pikvm_atx_power`, the latter from the power LED |
| `pikvm_gpio_*` | GPIO | two per channel, per direction |
| `pikvm_hw_*` | health | CPU, memory, temperatures, throttling flags |
| `pikvm_fan_*` | fan | `pikvm_fan_monitored`, plus the fan readings when one is fitted |

There is no MSD, streamer, HID or switch metric, and no ATX HDD LED. Read
those through the corresponding resource instead.

Only numbers are exported: booleans become `0`/`1`, and any string or `null`
in the underlying state is skipped silently. That is why a device with no fan
exports `pikvm_fan_monitored` alone, and why the set of `pikvm_hw_*` names
differs between boards.

### GPIO channel names

A channel lives in exactly one direction, so it produces one pair of metrics:
`pikvm_gpio_input_online_<channel>` and `pikvm_gpio_input_state_<channel>` for
an input, the `output` spelling for an output. Two rules apply to the channel
part of the name:

- channels whose name starts with `__` are hidden from the export;
- every character that is not a letter, digit or underscore is replaced with
  `_`, so `led-1` and `led.1` collide as `led_1`.

!!! warning "Upstream bug"
    kvmd fills **both** `pikvm_gpio_*_online_*` and `pikvm_gpio_*_state_*`
    from the channel's `state`, so the `online` metric does not report
    online-ness at all — it is a duplicate of `state`. Do not alert on it;
    read `gpio.get_state()` for a truthful `online` flag.

## Caching

kvmd caches the whole export server-side for **5 seconds**
(`alru_cache(maxsize=1, ttl=5)`). Scraping more often than that returns the
same body; a scrape interval below 5 s buys nothing.

## Integration example

```python
import asyncio

from aiopikvm import PiKVM


async def collect_metrics() -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        metrics = await kvm.prometheus.get_metrics()

        # Write to a file for the node_exporter textfile collector.
        with open("/var/lib/prometheus/pikvm.prom", "w") as file:
            file.write(metrics)


asyncio.run(collect_metrics())
```

## Full example

```python
import asyncio

from aiopikvm import PiKVM


async def main() -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        metrics = await kvm.prometheus.get_metrics()
        for line in metrics.splitlines():
            if line and not line.startswith("#"):
                name, value = line.split(maxsplit=1)
                print(f"{name:45} {value}")


asyncio.run(main())
```
