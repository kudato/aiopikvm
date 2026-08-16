# Redfish BMC

kvmd implements enough of the DMTF Redfish schema for generic BMC tooling to
read a machine's power state and change it. It is a subset in two directions:
`ComputerSystem` exposes power and nothing else, and the documents are plain
Redfish JSON rather than the `{"ok": true, "result": ...}` envelope the rest of
the API uses.

!!! warning
    Failures still arrive in the kvmd envelope, so they reach you as the usual
    [`APIError`][aiopikvm.APIError] and friends — only the successful bodies
    are plain Redfish.

## Service root

```python
root = await kvm.redfish.get_root()
print(root["RedfishVersion"])  # "1.6.0"
```

## Systems

```python
systems = await kvm.redfish.get_systems()
print(systems["Members@odata.count"])

system = await kvm.redfish.get_system()
print(system["PowerState"])  # "On" / "Off"
```

`Members` is a list of links, not of ids:

```python
{"Members": [{"@odata.id": "/redfish/v1/Systems/0"},
             {"@odata.id": "/redfish/v1/Systems/SwitchPort0"},
             {"@odata.id": "/redfish/v1/Systems/SwitchPort1"},
             {"@odata.id": "/redfish/v1/Systems/SwitchPort2"},
             {"@odata.id": "/redfish/v1/Systems/SwitchPort3"}]}
```

There is one for `"0"` when the ATX subsystem is enabled, and one per port of
an attached PiKVM Switch — four per switch unit, so the count goes 4, 8, 12 and
not 1, 2, 3. To feed them back to `get_system()`, take the tail of
each path:

```python
systems = await kvm.redfish.get_systems()
ids = [member["@odata.id"].rsplit("/", 1)[1] for member in systems["Members"]]
```

On a device with ATX disabled and no switch the collection is **empty** while
`Systems/0` still resolves — the collection lists what can be powered, not what
can be read.

### System ids

kvmd validates the id as a string and accepts exactly two forms:

| Id | Meaning |
| --- | --- |
| `"0"` | the machine PiKVM itself is wired to |
| `"SwitchPort<N>"` | port *N* of an attached PiKVM Switch, counted from 0 |

Redfish numbers the ports from 0 while the switch's own `id` starts at 1, so
the port labelled "1" in the switch UI is `SwitchPort0`.

Everything else is refused with HTTP 400 — `"1"` and `"00"` included, because
`"00"` is not the string `"0"`:

```python
await kvm.redfish.get_system("1")  # APIError: Missing or invalid Server ID
```

A well-formed port id on a device with no switch attached gets its own
message, `Non-existent Switch Port ID`.

## Update system

```python
await kvm.redfish.update_system(IndicatorLED="Lit")
```

!!! note
    kvmd accepts this and does nothing. The handler is a stub that answers
    HTTP 204, ignores the body and does not even look at the system id; it
    exists so that BMC tooling which PATCHes a system as part of its normal
    flow does not fail. A read straight afterwards returns exactly what it
    returned before, which is why the call returns `None`.

## Reset

`ComputerSystem.Reset` is the Redfish spelling of the ATX calls, and it acts on
real hardware.

!!! danger
    The default is `"ForceRestart"`, a press of the host's reset switch: no
    clean shutdown, no chance to flush anything to disk. Pass the reset type
    you mean.

```python
await kvm.redfish.reset("GracefulShutdown")
await kvm.redfish.reset("ForceOff", "SwitchPort0")  # a port of the switch
```

The call returns `None`. kvmd answers HTTP 204 with an empty body, and the
action is asynchronous besides — read the outcome from `PowerState`, or from
[`atx.get_state()`][aiopikvm.resources.atx.ATXResource.get_state]:

```python
await kvm.redfish.reset("On")
await asyncio.sleep(5)
print((await kvm.redfish.get_system())["PowerState"])
```

With the ATX subsystem disabled in the kvmd config, a reset of `"0"` still
answers 204 and does nothing at all, so there is no error to catch — check
`ATXState.enabled` first where that matters.

!!! danger
    A switch port is **not** covered by that. kvmd checks `enabled` only on the
    `"0"` branch; a `"SwitchPort<N>"` reset goes straight to the switch and
    acts on the port whatever `atx.type` is set to. On a device with the ATX
    plugin disabled *and* a switch attached — a normal configuration — the two
    ids behave completely differently.

    A reset also does **not** bounds-check the port: kvmd validates the form
    of the id and then drops a command for a port that does not exist, so
    `reset("ForceOff", "SwitchPort9")` on a four-port switch answers 204 and
    does nothing. `get_system("SwitchPort9")` does answer 400 — use it to
    check an id you did not build yourself. A port that is busy with an
    earlier click drops the command silently too, where `"0"` would raise
    `BusyError`.

### Reset types

kvmd accepts these six, matched **case-sensitively**:

```python
from aiopikvm.resources.redfish import RESET_TYPES

RESET_TYPES
# ("On", "ForceOn", "ForceOff", "GracefulShutdown",
#  "ForceRestart", "PushPowerButton")
```

Each one presses a front-panel switch, and all but the last are conditional on
the host's current power state:

| ResetType | What kvmd does | When |
| --- | --- | --- |
| `On` | short power click | only if the host is off |
| `ForceOn` | the same call as `On` | only if the host is off |
| `ForceOff` | power switch held down (5.5 s by default) | only if the host is on |
| `GracefulShutdown` | short power click, for the OS to act on | only if the host is on |
| `ForceRestart` | click on the **reset** switch | only if the host is on |
| `PushPowerButton` | short power click | no power-state condition |

Every click duration is configurable: `atx.click_delay` (0.1 s) and
`atx.long_click_delay` (5.5 s) for the machine PiKVM is wired to, per-port
settings for a switch. A switch's long click defaults to the same 5.5 s; its
short click and reset click default to 0.5 s rather than 0.1 s.

!!! note
    `ForceRestart` does not cut the power — it is the reset line. And the
    conditional types check the power state kvmd reads from the host's **power
    LED**, the same source as `PowerState`, so `reset("ForceRestart")` against
    a host kvmd believes to be off does nothing at all and still answers 204.
    Where that LED is miswired or unread, the conditional types become
    unpredictable — compare `PowerState` against reality before relying on
    them. The switch-port branch gates on the same LED, read per port.

The DMTF schema defines more — `GracefulRestart`, `Nmi`, `PowerCycle` — and
kvmd refuses all of them with HTTP 400 before taking any action, as it does a
wrong case such as `"forceoff"`. The live list is in every system document
under `Actions["#ComputerSystem.Reset"]["ResetType@Redfish.AllowableValues"]`.

!!! warning
    Every system document also advertises `#ComputerSystem.SetDefaultBootOrder`,
    which kvmd does not implement: POSTing to it answers a plain-text HTTP 404.

## Not implemented here

kvmd also serves `Managers`, `Managers/BMC` and the `VirtualMedia` collection.
aiopikvm has no methods for them yet ([#58](https://github.com/kudato/aiopikvm/issues/58));
until then reach them through [`PiKVM.request()`][aiopikvm.PiKVM.request].

## Full example

```python
import asyncio

from aiopikvm import PiKVM


async def main() -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        systems = await kvm.redfish.get_systems()
        for member in systems["Members"]:
            system_id = member["@odata.id"].rsplit("/", 1)[1]
            system = await kvm.redfish.get_system(system_id)
            print(f"{system_id}: {system['PowerState']}")

        # Uncomment to act on the host — this shuts somebody's machine down.
        # await kvm.redfish.reset("GracefulShutdown")
        # await asyncio.sleep(10)
        # print((await kvm.redfish.get_system())["PowerState"])


asyncio.run(main())
```
