"""MSD models."""

from typing import Any

from aiopikvm.models._base import _Base


class MSDDrive(_Base):
    """MSD virtual drive state."""

    image: str | None = None
    connected: bool
    cdrom: bool


class MSDStorage(_Base):
    """MSD storage information."""

    size: int
    free: int
    images: dict[str, Any]


class MSDState(_Base):
    """MSD subsystem state."""

    enabled: bool
    online: bool
    busy: bool
    drive: MSDDrive
    storage: MSDStorage
