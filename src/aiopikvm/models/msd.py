"""MSD models."""

from aiopikvm.models._base import _Base


class MSDImage(_Base):
    """An image stored in MSD storage.

    ``complete`` is ``False`` for an image whose upload was interrupted; kvmd
    keeps it in the listing so it can be resumed or removed.
    """

    complete: bool
    mod_ts: float
    removable: bool
    size: int
    writable: bool


class MSDDriveImage(MSDImage):
    """The image currently in the virtual drive.

    kvmd reports two fields here that the storage listing leaves out, because
    the drive can also hold an image that is not in storage at all.
    """

    name: str
    in_storage: bool


class MSDPart(_Base):
    """A partition of the MSD storage. The root one is keyed by ``""``."""

    free: int
    size: int
    writable: bool


class MSDUpload(_Base):
    """Progress of an image being written to storage.

    kvmd reports the same three fields in two places: under
    ``storage.uploading`` while a write is in flight, and as the body of the
    write endpoints themselves — once for :meth:`MSDResource.upload`, once
    per progress record for :meth:`MSDResource.upload_remote`.

    ``name`` is the name kvmd stored the image under, which is not
    necessarily the one that was asked for: a ``prefix`` is joined on and the
    whole thing goes through kvmd's file-name validator. ``size`` is the
    total the write was opened for — the request's ``Content-Length``, or the
    remote's — and ``written`` how much of it has landed.
    """

    name: str
    size: int
    written: int


class MSDDownload(_Base):
    """Progress of a stored image being read back.

    ``readed`` is spelled the way kvmd spells it on the wire.
    """

    name: str
    size: int
    readed: int


class MSDStorage(_Base):
    """MSD storage: what is on it and what is moving in or out of it."""

    images: dict[str, MSDImage]
    parts: dict[str, MSDPart]
    downloading: MSDDownload | None = None
    uploading: MSDUpload | None = None


class MSDDrive(_Base):
    """The virtual drive presented to the target host."""

    cdrom: bool
    connected: bool
    rw: bool
    image: MSDDriveImage | None = None


class MSDState(_Base):
    """MSD subsystem state.

    ``drive`` and ``storage`` are both ``None`` while the subsystem is
    offline — the MSD is disabled in the OTG profile, or kvmd has not
    finished setting it up. Neither is available without the other.
    """

    enabled: bool
    online: bool
    busy: bool
    drive: MSDDrive | None = None
    storage: MSDStorage | None = None
