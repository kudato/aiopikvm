# KVM Switch

The Switch resource manages multi-port KVM switching and EDID profiles.

## Get state

```python
state = await kvm.switch.get_state()

if state.summary.active_port < 0:
    print("No port selected")
else:
    print(f"Active port: {state.summary.active_id}")

for port in state.model.ports:
    print(f"Port {port.id}: {port.name} (unit {port.unit}, channel {port.channel})")
```

`summary.active_port` is the numeric index — `-1` when nothing is selected —
while `summary.active_id` is the label the web UI shows (`"3"` on a single
unit, `"2.3"` on a chain). `summary.synced` is `False` while the units are
still catching up with the configuration kvmd wants them in.

The per-port readings are parallel lists, indexed by port number:

```python
for i, port in enumerate(state.model.ports):
    print(f"{port.name}: video={state.video.links[i]}, usb={state.usb.links[i]}")
    print(f"  power LED: {state.atx.leds.power[i]}, busy: {state.atx.busy[i]}")
```

Everything is empty on a PiKVM with no switch attached.

## Switch active port

Ports are addressed by number, counting from `0` across the whole chain. On a
multi-unit chain the `unit.port` form selects the same ports. The `id` strings
from the state are display labels and are not accepted here:

```python
await kvm.switch.set_active(1)

# On a chain: unit 1, port 3
await kvm.switch.set_active(1.3)
```

## EDID management

### List stored EDIDs

The catalogue is part of the switch state; `get_edids()` is a shortcut to it:

```python
edids = await kvm.switch.get_edids()
for edid_id, edid in edids.items():
    monitor = edid.parsed.monitor_name if edid.parsed else "unparsed"
    print(f"{edid_id}: {edid.name} — {monitor}")
```

### Create an EDID

kvmd generates the id and returns it:

```python
edid_id = await kvm.switch.create_edid("Custom 1080p", "00FFFFFFFFFFFF00...")
```

`data` is the EDID blob as hex, 256 or 512 characters.

### Assign an EDID to a port

This is a port parameter, not an EDID operation:

```python
await kvm.switch.set_port_params(0, edid_id=edid_id)
```

### Rename or replace an EDID

```python
await kvm.switch.change_edid(edid_id, name="Renamed")
await kvm.switch.change_edid(edid_id, data="00FFFFFFFFFFFF00...")
```

### Remove an EDID

```python
await kvm.switch.remove_edid(edid_id)
```

The built-in `"default"` EDID can be neither changed nor removed.

## Quick port switching

```python
# Switch to previous port
await kvm.switch.set_active_prev()

# Switch to next port
await kvm.switch.set_active_next()
```

## Beacon indicators

A beacon call targets exactly one thing — a port, a unit's uplink, or a unit's
downlink. There is no "all beacons off" call:

```python
# Light the beacon of port 3
await kvm.switch.set_beacon(True, port=3)

# Extinguish it again
await kvm.switch.set_beacon(False, port=3)

# The uplink beacon of unit 1
await kvm.switch.set_beacon(True, uplink=1)
```

## Indicator colours

Five roles, each `RRGGBB:BB:IIII` (colour, brightness, blink interval in
milliseconds) or `"default"`:

```python
await kvm.switch.set_colors(
    active="00FF00:80:0000",      # steady green for the selected port
    beacon="FFA500:BF:0028",      # blinking orange for a lit beacon
    inactive="default",
)
```

Roles left out keep their current colour. The current ones are in
`state.colors`.

## Port configuration

```python
await kvm.switch.set_port_params(
    0,
    name="Server1",
    edid_id=edid_id,
    dummy=True,
    atx_click_power_delay=1.5,
)
```

The allowed delay ranges are in `state.model.limits.atx.click_delays`.

## ATX power control per port

```python
# Power actions: "on", "off", "off_hard", "reset_hard"
await kvm.switch.atx_power(0, "on")

# Button clicks: "power", "power_long", "reset"
await kvm.switch.atx_click(0, "power")
```

## Reboot switch unit

```python
# Reboot unit 0
await kvm.switch.reset(0)

# Reboot into bootloader (reflashing mode)
await kvm.switch.reset(1, bootloader=True)
```

## Full example

```python
import asyncio
from aiopikvm import PiKVM

async def main():
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        state = await kvm.switch.get_state()
        active = state.summary.active_port

        for i, port in enumerate(state.model.ports):
            marker = " (active)" if i == active else ""
            print(f"  {port.id}: {port.name}{marker}")

        # Switch to the next port in the chain
        if state.model.ports:
            await kvm.switch.set_active((active + 1) % len(state.model.ports))

asyncio.run(main())
```
