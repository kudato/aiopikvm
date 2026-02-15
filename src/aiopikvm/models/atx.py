"""ATX models."""

from aiopikvm.models._base import _Base


class ATXLeds(_Base):
    """ATX LED indicators state."""

    power: bool
    hdd: bool


class ATXState(_Base):
    """ATX subsystem state."""

    enabled: bool
    busy: bool
    leds: ATXLeds
