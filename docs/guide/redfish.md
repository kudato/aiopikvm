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

`Members` holds `"0"` when the ATX subsystem is enabled, plus one
`"SwitchPort<N>"` per port of an attached PiKVM Switch. On a device with ATX
disabled and no switch the collection is **empty** while `Systems/0` still
resolves — the collection lists what can be powered, not what can be read.

### System ids

kvmd validates the id as a string and accepts exactly two forms:

| Id | Meaning |
| --- | --- |
| `"0"` | the machine PiKVM itself is wired to |
| `"SwitchPort<N>"` | port *N* of an attached PiKVM Switch |

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
    The default is `"ForceRestart"`: it cuts the power and brings it back,
    giving the host no chance to shut down cleanly. Pass the reset type you
    mean.

```python
await kvm.redfish.reset("GracefulShutdown")
await kvm.redfish.reset("ForceOff", "SwitchPort1")  # a port of the switch
```

The call returns `None`. kvmd answers HTTP 204 with an empty body, and the
action is asynchronous besides — read the outcome from `PowerState`, or from
[`atx.get_state()`][aiopikvm.resources.atx.ATXResource.get_state]:

```python
await kvm.redfish.reset("On")
await asyncio.sleep(5)
print((await kvm.redfish.get_system())["PowerState"])
```

With the ATX subsystem disabled in the kvmd config the reset still answers 204
and does nothing at all, so there is no error to catch. Check
`ATXState.enabled` first where that matters.

### Reset types

kvmd accepts these six, matched **case-sensitively**:

```python
from aiopikvm.resources.redfish import RESET_TYPES

RESET_TYPES
# ("On", "ForceOn", "ForceOff", "GracefulShutdown",
#  "ForceRestart", "PushPowerButton")
```

| ResetType | Effect |
| --- | --- |
| `On` | power on |
| `ForceOn` | power on |
| `ForceOff` | cut the power |
| `GracefulShutdown` | short power-button press, letting the OS shut down |
| `ForceRestart` | cut the power and restore it |
| `PushPowerButton` | short power-button press |

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
        system = await kvm.redfish.get_system()
        print(f"Power before: {system['PowerState']}")

        await kvm.redfish.reset("GracefulShutdown")
        await asyncio.sleep(10)

        system = await kvm.redfish.get_system()
        print(f"Power after: {system['PowerState']}")


asyncio.run(main())
```
