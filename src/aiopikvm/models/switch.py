"""Switch models."""

from aiopikvm.models._base import _Base


class SwitchPort(_Base):
    """KVM switch port."""

    name: str


class SwitchState(_Base):
    """KVM switch state."""

    active: str
    ports: dict[str, SwitchPort]


class EDID(_Base):
    """EDID profile."""

    id: str
    data: str
    description: str | None = None
