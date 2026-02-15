"""GPIO models."""

from aiopikvm.models._base import _Base


class GPIOChannel(_Base):
    """GPIO output channel state."""

    online: bool
    state: bool
    busy: bool


class GPIOInput(_Base):
    """GPIO input channel state."""

    online: bool
    state: bool


class GPIOState(_Base):
    """GPIO subsystem state."""

    inputs: dict[str, GPIOInput]
    outputs: dict[str, GPIOChannel]
