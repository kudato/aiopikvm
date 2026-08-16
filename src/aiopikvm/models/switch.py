"""Switch models."""

from aiopikvm.models._base import _Base


class SwitchFirmware(_Base):
    """Protocol version the switch subsystem speaks.

    A constant of the kvmd build, unrelated to the firmware running on the
    units — that one is :class:`SwitchUnitFirmware`.
    """

    version: int


class SwitchUnitFirmware(_Base):
    """Firmware running on one physical unit."""

    version: int
    devbuild: bool


class SwitchUnit(_Base):
    """One switch unit in the chain."""

    firmware: SwitchUnitFirmware


class SwitchAtxClickDelays(_Base):
    """How long each ATX button is held, in seconds."""

    power: float
    power_long: float
    reset: float


class SwitchPortAtx(_Base):
    """ATX configuration of a port."""

    click_delays: SwitchAtxClickDelays


class SwitchPortVideo(_Base):
    """Video configuration of a port."""

    dummy: bool


class SwitchPort(_Base):
    """A port of the switch chain.

    ``id`` is what the web UI shows: ``"3"`` on a single unit, ``"2.3"`` once
    more than one unit is chained. Ports are addressed by their numeric index
    everywhere in the API, not by this string.
    """

    unit: int
    channel: int
    name: str
    id: str
    atx: SwitchPortAtx
    video: SwitchPortVideo


class SwitchAtxClickDelayLimit(_Base):
    """Allowed range for one ATX click delay."""

    default: float
    min: float
    max: float


class SwitchAtxClickDelayLimits(_Base):
    """Allowed ranges for the three ATX click delays."""

    power: SwitchAtxClickDelayLimit
    power_long: SwitchAtxClickDelayLimit
    reset: SwitchAtxClickDelayLimit


class SwitchAtxLimits(_Base):
    """ATX limits of the switch."""

    click_delays: SwitchAtxClickDelayLimits


class SwitchLimits(_Base):
    """What the switch accepts for the tunable port parameters."""

    atx: SwitchAtxLimits


class SwitchModel(_Base):
    """The static half of the switch state.

    ``units`` and ``ports`` are empty until the units have reported in, and
    stay empty on a PiKVM with no switch attached.
    """

    firmware: SwitchFirmware
    units: list[SwitchUnit]
    ports: list[SwitchPort]
    limits: SwitchLimits


class SwitchSummary(_Base):
    """Which port is currently selected.

    ``active_port`` is ``-1`` when nothing is selected, and ``active_id`` is
    then an empty string. ``synced`` is ``False`` while the units are still
    catching up with the state kvmd wants them in.
    """

    active_port: int
    active_id: str
    synced: bool


class EDIDInfo(_Base):
    """The fields kvmd decodes out of an EDID blob.

    ``monitor_name`` and ``monitor_serial`` are ``None`` when the blob has no
    descriptor block for them.
    """

    mfc_id: str
    product_id: int
    serial: int
    monitor_name: str | None = None
    monitor_serial: str | None = None
    audio: bool


class EDID(_Base):
    """An EDID the switch can present to a port.

    ``parsed`` is ``None`` when kvmd could not decode the blob.
    """

    name: str
    data: str
    parsed: EDIDInfo | None = None


class SwitchEdids(_Base):
    """The EDID catalogue.

    ``all`` is keyed by EDID id — ``"default"`` always exists — and ``used``
    lists the id in effect on each port, in port order.
    """

    all: dict[str, EDID]
    used: list[str]


class SwitchColor(_Base):
    """One indicator colour.

    ``blink_ms`` of ``0`` means a steady light.
    """

    red: int
    green: int
    blue: int
    brightness: int
    blink_ms: int


class SwitchColors(_Base):
    """Indicator colours, one per port role."""

    inactive: SwitchColor
    active: SwitchColor
    flashing: SwitchColor
    beacon: SwitchColor
    bootloader: SwitchColor


class SwitchLinks(_Base):
    """Per-port link sensors, in port order."""

    links: list[bool]


class SwitchBeacons(_Base):
    """Which beacons are lit: one flag per port, and one per unit link."""

    uplinks: list[bool]
    downlinks: list[bool]
    ports: list[bool]


class SwitchAtxLeds(_Base):
    """ATX LED readings, one entry per port."""

    power: list[bool]
    hdd: list[bool]


class SwitchAtx(_Base):
    """ATX state of every port, in port order."""

    busy: list[bool]
    leds: SwitchAtxLeds


class SwitchState(_Base):
    """KVM switch state.

    Mirrors ``GET /api/switch``. Every list is indexed by port number, and all
    of them are empty on a PiKVM without a switch — which is also the only
    configuration the fixtures cover.
    """

    model: SwitchModel
    summary: SwitchSummary
    edids: SwitchEdids
    colors: SwitchColors
    video: SwitchLinks
    usb: SwitchLinks
    beacons: SwitchBeacons
    atx: SwitchAtx
