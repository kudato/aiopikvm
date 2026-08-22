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
an attached PiKVM Switch — four per switch unit, so the port count goes 4, 8,
12 and not 1, 2, 3. To feed them back to `get_system()`, take the tail of
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
    earlier click on the same line drops the command silently too, where `"0"`
    would raise `BusyError`. kvmd holds the power line and the reset line
    independently, so a `ForceRestart` is not blocked by a running power
    click.

### Reset types

kvmd accepts these six, matched **case-sensitively** — unlike the output,
button and compression names elsewhere in this client, which it lowercases
first. `reset()` takes them as the `ResetType` type, so a name from the wider
DMTF schema is a type error rather than an HTTP 400, and `RESET_TYPES` is the
same list to check against at runtime:

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

## Managers

The other half of kvmd's Redfish tree is the BMC itself — PiKVM — and the
virtual media it offers the host.

```python
managers = await kvm.redfish.get_managers()
# {"Members": [{"@odata.id": "/redfish/v1/Managers/BMC"}], ...}

manager = await kvm.redfish.get_manager()
print(manager["Id"])  # "BMC"
```

There is exactly one, and kvmd writes its path into the route table as a
literal rather than a parameter — which is why `get_manager()` takes no id,
unlike `get_system()`. The collection exists for a client that walks the tree.

## Virtual media

`VirtualMedia/MSD` is the Redfish view of the mass storage drive, and a
narrower one than [`msd.get_state()`][aiopikvm.resources.msd.MSDResource.get_state]:

```python
collection = await kvm.redfish.get_virtual_media_collection()
# {"Members": [{"@odata.id": "/redfish/v1/Managers/BMC/VirtualMedia/MSD"}], ...}

media = await kvm.redfish.get_virtual_media()
print(media["ImageName"], media["Inserted"], media["WriteProtected"])
print(media["Oem"]["PiKVM"])
# {"MsdEnabled": true, "MsdOnline": false, "MsdBusy": false, "DriveOptical": null}
```

!!! warning
    kvmd reads the drive fields only while the drive is **online**, so on an
    offline MSD every one of them is `null` — `Inserted: null` means "not
    known", not "no". `Oem.PiKVM.MsdOnline` is what tells the two apart, and
    the only field worth branching on before the rest are read.

### Insert and eject

```python
await kvm.redfish.insert_media("ubuntu.iso")
await kvm.redfish.eject_media()
```

`insert_media()` ejects whatever is connected, selects the image and connects
the drive again; `eject_media()` disconnects it and clears the selection. Both
answer HTTP 204 with an empty body and return `None` — read the result back
from `get_virtual_media()`.

!!! danger
    Despite the `Image@Redfish.AllowableValues: ["URI"]` in the document,
    kvmd reads `Image` as the name of an **image already in MSD storage** —
    the same name
    [`msd.set_params()`][aiopikvm.resources.msd.MSDResource.set_params]
    takes — and hands it straight to that call. A URL is not refused for
    being one: kvmd's validator splits the argument on `/` and checks each
    part as a filename, so a URL passes as a multi-part path and reaches the
    MSD as a name nothing matches. Upload the image first, or use
    [`msd.upload_remote()`][aiopikvm.resources.msd.MSDResource.upload_remote].

`inserted=False` selects the image and leaves the drive disconnected, and
`write_protected` is Redfish's spelling of the inverse of kvmd's `rw`:

```python
await kvm.redfish.insert_media("win.img", inserted=False, write_protected=False)
```

### Two kvmd defects on this path

Both were recorded from a device running kvmd 4.206, and both matter enough to
route around rather than discover in production.

**An inserted `.iso` is not a CD-ROM.** kvmd picks the drive type with
`name.lower().startswith(".iso")` — `startswith`, not `endswith`. No ordinary
filename begins with a file extension, so this branch always mounts a flash
drive, whatever the image is called. Where the host needs an optical drive, go
through the MSD API instead:

```python
await kvm.msd.set_params(image="ubuntu.iso", cdrom=True)
await kvm.msd.set_connected(True)
```

**An offline MSD answers HTTP 500 with an empty error block.** kvmd reads
`state.get("drive", {}).get("connected")` before it checks `online`, and an
offline MSD reports `drive` as `null` — the key is present, so the default
never applies and the attribute lookup raises. The failure carries no error
name and no message, so nothing in it says which subsystem broke:

```python
media = await kvm.redfish.get_virtual_media()
if not media["Oem"]["PiKVM"]["MsdOnline"]:
    raise RuntimeError("MSD is not set up on this device")
await kvm.redfish.insert_media("ubuntu.iso")
```

`eject_media()` is not affected — it reaches kvmd's own MSD plugin and comes
back as a proper HTTP 400 `MsdOfflineError`.

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
