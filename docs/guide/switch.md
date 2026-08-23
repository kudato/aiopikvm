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

!!! warning "With no unit attached, every port command succeeds and does nothing"
    kvmd validates the shape of a port argument and then hands the command to
    a chain that has nowhere to send it. `set_active()`, `set_active_prev()`,
    `set_active_next()`, `set_beacon()`, `set_port_params()`, `reset()`,
    `atx_power()` and `atx_click()` all answer HTTP 200 on a device with no
    switch, and there is no way to tell that apart from a command that
    landed. `model.units` is what says whether there is anything there:

    ```python
    state = await kvm.switch.get_state()
    if not state.model.units:
        raise RuntimeError("no switch is attached to this device")
    await kvm.switch.set_active(1)
    ```

    Two parts of this API are not affected, because they are storage rather
    than hardware: [EDID management](#edid-management) and
    [indicator colours](#indicator-colours) work, and read back, on a device
    with no switch. `set_port_params()` sits in between — it is stored and it
    survives a restart, but port names are exposed only inside `model.ports`,
    which is empty, so nothing reads it back until a unit is attached.

## Switch active port

Ports are addressed by number, counting from `0` across the whole chain. On a
multi-unit chain the `unit.port` form selects the same ports, and it is 1-based
in both halves:

```python
await kvm.switch.set_active(1)

# On a chain: unit 1, port 3
await kvm.switch.set_active(1.3)
```

The `id` in the state is that same 1-based label. On a chain `float(port.id)`
therefore addresses the port it belongs to, but on a single unit `id` is one
greater than the port's index — `"3"` is `model.ports[2]` — so a position taken
from `model.ports` is the form that means the same thing on both.

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

await kvm.switch.change_edid(edid_id)   # ConfigurationError
```

A call with neither is refused here rather than sent: kvmd answers it with
success and changes nothing. It raises
[`ConfigurationError`](error-handling.md), which is **not** an `APIError` —
nothing was sent, so there is no status to carry — and an `except APIError`
around device calls does not catch it. `set_beacon()` and `set_colors()` below
have a refusal of their own.

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

# The downlink beacon of unit 1
await kvm.switch.set_beacon(True, downlink=1)

await kvm.switch.set_beacon(True)              # ConfigurationError
await kvm.switch.set_beacon(True, port=3, uplink=1)   # ConfigurationError
```

Not exactly one target is refused before anything is sent. kvmd checks the
three in the order `port`, `uplink`, `downlink` and falls through to
`downlink` when none is present, which answers 400 — so the call that names
two would silently act on the first of them.

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

Roles left out keep their current colour, and a call with no role at all is
refused before anything is sent:

```python
await kvm.switch.set_colors()   # ConfigurationError
```

The current ones are in `state.colors`, as `SwitchColor` models with integer
components rather than as the strings this call takes — so a read cannot be
handed back to it unchanged. Formatting one:

```python
from aiopikvm import SwitchColor


def as_param(colour: SwitchColor) -> str:
    return (
        f"{colour.red:02X}{colour.green:02X}{colour.blue:02X}"
        f":{colour.brightness:02X}:{colour.blink_ms:04X}"
    )

state = await kvm.switch.get_state()
await kvm.switch.set_colors(active=as_param(state.colors.beacon))
```

Passing the model itself raises nothing locally — httpx stringifies it and
sends `red=0 green=255 …` — and comes back as an HTTP 400 `ValidatorError`
several layers away from the mistake.

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

Both are typed — `ATXAction` and `ATXButton` — and both are kvmd's ATX
vocabulary rather than the switch's own: the same names serve `/api/atx`,
where [`ATXResource`](atx.md) spells each one out as a method of its own.

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
