# Prometheus Metrics

The Prometheus resource exports PiKVM metrics in Prometheus text exposition format.

## Get metrics

```python
metrics = await kvm.prometheus.get_metrics()
print(metrics)
```

The method returns a string in the Prometheus text exposition format:

```text
# HELP pikvm_atx_enabled ATX enabled
# TYPE pikvm_atx_enabled gauge
pikvm_atx_enabled 1
# HELP pikvm_atx_power ATX power LED
# TYPE pikvm_atx_power gauge
pikvm_atx_power 1
...
```

## Integration example

You can use a Prometheus client library to push metrics, or serve them as an HTTP endpoint:

```python
import asyncio
from aiopikvm import PiKVM

async def collect_metrics():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        metrics = await kvm.prometheus.get_metrics()

        # Write to a file for node_exporter textfile collector
        with open("/var/lib/prometheus/pikvm.prom", "w") as f:
            f.write(metrics)

asyncio.run(collect_metrics())
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        metrics = await kvm.prometheus.get_metrics()
        for line in metrics.strip().split("\n"):
            if not line.startswith("#"):
                print(line)

asyncio.run(main())
```
