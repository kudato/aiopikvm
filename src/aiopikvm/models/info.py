"""Info models.

These follow the per-submanager shape — the one
[`SystemResource.get_info()`][aiopikvm.resources.system.SystemResource.get_info]
returns with ``legacy=False``, and the one the WebSocket ``info`` events
carry. The legacy shape is a rearrangement of it and is not modelled: it
moves ``platform`` out of ``system`` into a synthetic ``hw``, which no
submanager produces.

Everything kvmd fills in from something outside itself may come back
``None``: a reading it could not take, a file it could not parse, a daemon
that did not answer. Those are typed as nullable rather than dropped,
because a device reports them that way in ordinary operation — a PiKVM
whose ``vcgencmd`` is missing has no throttling block at all.
"""

from typing import Any

from aiopikvm.models._base import _Base


class InfoAuth(_Base):
    """Whether kvmd requires authentication."""

    enabled: bool


class InfoNode(_Base):
    """Host name of the device itself, as its kernel reports it."""

    host: str


class InfoUptimeParts(_Base):
    """The uptime split up, so a caller does not have to divide."""

    days: int
    hours: int
    minutes: int
    seconds: int


class InfoUptime(_Base):
    """How long the device has been up."""

    total: int
    parts: InfoUptimeParts


class InfoTemp(_Base):
    """Temperature readings, in degrees Celsius."""

    cpu: float | None


class InfoCPU(_Base):
    """CPU load, as a whole-number percentage."""

    percent: float | None


class InfoMem(_Base):
    """Memory use. All three are ``None`` together when the read fails."""

    percent: float | None
    total: int | None
    available: int | None


class InfoThrottlingFlag(_Base):
    """One throttling condition, now and since boot.

    ``past`` stays ``True`` once it has happened, which is why kvmd has an
    ``ignore_past`` setting for it.
    """

    now: bool
    past: bool


class InfoThrottlingFlags(_Base):
    """The three conditions a Raspberry Pi reports."""

    undervoltage: InfoThrottlingFlag
    freq_capped: InfoThrottlingFlag
    throttled: InfoThrottlingFlag


class InfoThrottling(_Base):
    """Throttling state, decoded from the firmware's bit field."""

    raw_flags: int
    parsed_flags: InfoThrottlingFlags
    ignore_past: bool


class InfoHealth(_Base):
    """Load, temperature and throttling.

    ``throttling`` is ``None`` wherever ``vcgencmd`` cannot be run — kvmd
    reads it from the Raspberry Pi firmware and has no other source.
    """

    temp: InfoTemp
    cpu: InfoCPU
    mem: InfoMem
    throttling: InfoThrottling | None


class InfoFan(_Base):
    """Fan controller state.

    ``state`` is whatever the ``kvmd-fan`` daemon answers on its own socket,
    so it is left untyped: it belongs to another program, and a device
    without that daemon reports ``monitored`` false and ``state`` ``None``.
    The same ``None`` also means kvmd asked and got no answer.
    """

    monitored: bool
    state: dict[str, Any] | None


class InfoKvmd(_Base):
    """The kvmd version, which is what this client's floor is stated in."""

    version: str


class InfoKernel(_Base):
    """``uname`` of the device."""

    system: str
    release: str
    version: str
    machine: str


class InfoStreamer(_Base):
    """The streamer binary kvmd is configured to run.

    ``version`` is an empty string and ``features`` an empty mapping when
    kvmd could not run it — not ``None``, which is why neither is nullable.
    """

    app: str
    version: str
    features: dict[str, bool]


class InfoPlatform(_Base):
    """What the device is.

    ``base`` and ``serial`` come from the device tree and ``model``,
    ``video`` and ``board`` from kvmd's platform file; any of the five is
    ``None`` when the file behind it could not be read. ``type`` is a
    constant in kvmd's source, not a reading.
    """

    type: str
    base: str | None
    serial: str | None
    model: str | None
    video: str | None
    board: str | None


class InfoSystem(_Base):
    """Versions and hardware identity."""

    kvmd: InfoKvmd
    streamer: InfoStreamer
    kernel: InfoKernel
    platform: InfoPlatform


class InfoExtra(_Base):
    """One entry of the extras catalogue.

    An extra is a ``manifest.yaml`` shipped beside kvmd, so its contents are
    whatever its author wrote and every field here is optional. kvmd itself
    only writes two pairs into it: ``enabled`` and ``started`` when the
    manifest names a ``daemon``, and ``port`` resolved to an integer when the
    manifest names one as a config path. Anything else the manifest carries
    is kept as an extra attribute.
    """

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    path: str | None = None
    place: int | None = None
    daemon: str | None = None
    port: int | None = None
    enabled: bool | None = None
    started: bool | None = None


class InfoState(_Base):
    """Device information, one attribute per kvmd submanager.

    Every field is optional, for two reasons that look the same from here:
    [`get_info()`][aiopikvm.resources.system.SystemResource.get_info] can ask
    for a subset, and the WebSocket sends one submanager per event, so a
    snapshot taken early has only what has arrived. ``meta`` and ``extras``
    are also nullable at the source — kvmd returns ``None`` for either when
    it cannot parse the files behind them — and that is indistinguishable
    here from not having been asked for.

    ``meta`` is left untyped on purpose. It is a YAML file the device's owner
    writes, and kvmd reads exactly one thing out of it: it replaces
    ``server.host`` when that is set to ``@auto``. Nothing else about its
    shape is kvmd's to promise.
    """

    auth: InfoAuth | None = None
    extras: dict[str, InfoExtra] | None = None
    fan: InfoFan | None = None
    health: InfoHealth | None = None
    meta: dict[str, Any] | None = None
    node: InfoNode | None = None
    system: InfoSystem | None = None
    uptime: InfoUptime | None = None
