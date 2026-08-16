"""ATX models."""

from aiopikvm.models._base import _Base


class ATXLeds(_Base):
    """ATX LED indicators state."""

    power: bool
    hdd: bool


class ATXActs(_Base):
    """Which ATX action is running right now.

    kvmd guards the power and reset lines separately, so a reset can be
    pending while the power line is free. ``ATXState.busy`` is the two of
    them combined.
    """

    power: bool
    reset: bool


class ATXState(_Base):
    """ATX subsystem state.

    ``enabled`` is ``False`` when the ATX plugin is disabled, in which case
    every action answers HTTP 400.
    """

    enabled: bool
    busy: bool
    acts: ATXActs
    leds: ATXLeds
