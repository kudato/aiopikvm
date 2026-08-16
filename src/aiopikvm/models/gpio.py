"""GPIO models."""

from typing import Any

from aiopikvm.models._base import _Base


class GPIOHardware(_Base):
    """Driver and pin backing a channel."""

    driver: str
    pin: str


class GPIOPulse(_Base):
    """Pulse limits of an output channel.

    A ``delay`` of ``0`` means the channel does not support pulsing at all —
    kvmd answers ``GpioPulseNotSupported``.
    """

    delay: float
    min_delay: float
    max_delay: float


class GPIOInputScheme(_Base):
    """Configuration of an input channel.

    No capture covers this: the fixture device has no input channels. The
    shape comes from kvmd's ``ugpio.py``, which builds it two lines away from
    the output scheme below.
    """

    hw: GPIOHardware


class GPIOOutputScheme(_Base):
    """Configuration of an output channel."""

    switch: bool
    pulse: GPIOPulse
    hw: GPIOHardware


class GPIOScheme(_Base):
    """Channels kvmd is configured with, regardless of their current state."""

    inputs: dict[str, GPIOInputScheme]
    outputs: dict[str, GPIOOutputScheme]


class GPIOViewHeader(_Base):
    """Header of the GPIO widget."""

    title: list[dict[str, Any]]


class GPIOView(_Base):
    """Layout hints for the GPIO widget in the PiKVM web UI.

    The items are deliberately left as dictionaries. kvmd emits three shapes
    here — ``label``, ``input`` and ``output``, told apart by ``type`` — and
    the captured device has an empty table, so a typed union would rest on
    kvmd's source alone and no test could hold it honest. This is layout
    metadata for the web UI; nothing in this client reads it. A ``None`` row
    is a separator.
    """

    header: GPIOViewHeader
    table: list[list[dict[str, Any]] | None]


class GPIOModel(_Base):
    """The static half of the GPIO state: what exists and how it is drawn."""

    scheme: GPIOScheme
    view: GPIOView


class GPIOInput(_Base):
    """GPIO input channel state."""

    online: bool
    state: bool


class GPIOChannel(_Base):
    """GPIO output channel state.

    ``busy`` is ``True`` while a switch or pulse is still running; kvmd
    reports ``state`` as ``False`` for the duration.
    """

    online: bool
    state: bool
    busy: bool


class GPIOIOState(_Base):
    """Current readings of every configured channel."""

    inputs: dict[str, GPIOInput]
    outputs: dict[str, GPIOChannel]


class GPIOState(_Base):
    """GPIO subsystem state.

    Mirrors ``GET /api/gpio``: ``model`` describes the channels and the web
    UI layout, ``state`` holds their readings. ``inputs`` and ``outputs`` are
    shortcuts to the readings, which is what callers almost always want.

    This is the shape of the REST response. The ``gpio`` WebSocket events
    carry partial updates and do not validate against it.
    """

    model: GPIOModel
    state: GPIOIOState

    @property
    def inputs(self) -> dict[str, GPIOInput]:
        """Readings of the input channels."""
        return self.state.inputs

    @property
    def outputs(self) -> dict[str, GPIOChannel]:
        """Readings of the output channels."""
        return self.state.outputs
