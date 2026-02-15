"""HID models."""

from aiopikvm.models._base import _Base


class HIDMouse(_Base):
    """HID mouse state."""

    absolute: bool
    connected: bool


class HIDKeyboard(_Base):
    """HID keyboard state."""

    connected: bool


class HIDState(_Base):
    """HID subsystem state."""

    online: bool
    busy: bool
    connected: bool | None = None
    keyboard: HIDKeyboard
    mouse: HIDMouse


class HIDKeymap(_Base):
    """Keyboard keymap entry."""

    name: str
