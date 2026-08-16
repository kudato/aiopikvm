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
    """Mouse jiggler — the anti-idle mover built into kvmd."""

    enabled: bool
    active: bool
    interval: int


class HIDState(_Base):
    """HID subsystem state.

    Mirrors the shape returned by ``GET /api/hid``. ``connected`` reports
    whether the target host has the HID plugged in, and only MCU-based
    backends can tell — it is ``None`` on OTG.
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
    :pymethod:`HIDResource.type_text` accepts as its ``keymap`` argument.
    """

    default: str
    available: list[str]


class _HIDInactivity(_Base):
    """Envelope of ``GET /api/hid/inactivity``, unwrapped by the resource."""

    inactivity: int
