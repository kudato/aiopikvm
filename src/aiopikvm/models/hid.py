"""HID models."""

from aiopikvm.models._base import _Base


class HIDOutputs(_Base):
    """Selectable HID output modes for one device.

    ``available`` is empty and ``active`` is an empty string on backends that
    cannot switch modes at runtime — the OTG keyboard, for instance.
    """

    active: str
    available: list[str]


class HIDKeyboardLeds(_Base):
    """Keyboard LED state as reported by the target host."""

    caps: bool
    num: bool
    scroll: bool


class HIDKeyboard(_Base):
    """HID keyboard state."""

    online: bool
    leds: HIDKeyboardLeds
    outputs: HIDOutputs


class HIDMouse(_Base):
    """HID mouse state."""

    online: bool
    absolute: bool
    outputs: HIDOutputs


class HIDJiggler(_Base):
    """Mouse jiggler — the anti-idle mover built into kvmd.

    Two flags that read alike and are not the same.
    [`HIDResource.set_params()`][aiopikvm.resources.hid.HIDResource.set_params]
    with ``jiggler`` writes ``active``, which is whether it is running now.
    ``enabled`` says the device was configured with a jiggler at all and no
    API call moves it, so a caller who checks ``enabled`` after a write sees
    it unchanged and concludes the write was ignored.

    ``interval`` is the idle time in seconds before it starts nudging the
    pointer, and is likewise read-only over the API.
    """

    enabled: bool
    active: bool
    interval: int


class HIDState(_Base):
    """HID subsystem state.

    Mirrors the shape returned by ``GET /api/hid``. ``connected`` reports
    whether the target host has the HID plugged in, and only the MCU-based
    backends can tell — ``otg``, ``ch9329`` and ``bt`` report ``None``. The
    MCU backends are also the only ones that implement
    [`HIDResource.set_connected()`][aiopikvm.resources.hid.HIDResource.set_connected],
    so a ``bool`` here says that call does something. A ``None`` does not say
    the reverse: an MCU backend reports it too until its microcontroller has
    sent a status word carrying the flag.
    """

    enabled: bool
    online: bool
    busy: bool
    connected: bool | None = None
    keyboard: HIDKeyboard
    mouse: HIDMouse
    jiggler: HIDJiggler


class HIDKeymaps(_Base):
    """Keyboard layouts installed on the device.

    Returned by ``GET /api/hid/keymaps``; the names are what
    [`HIDResource.type_text()`][aiopikvm.resources.hid.HIDResource.type_text]
    accepts as its ``keymap`` argument.
    """

    default: str
    available: list[str]


class _HIDInactivity(_Base):
    """Envelope of ``GET /api/hid/inactivity``, unwrapped by the resource."""

    inactivity: int
