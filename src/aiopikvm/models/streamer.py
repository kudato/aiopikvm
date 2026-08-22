"""Streamer models."""

from pydantic import Field

from aiopikvm.models._base import _Base


class Resolution(_Base):
    """Video resolution."""

    width: int
    height: int


class StreamerSource(_Base):
    """Streamer video source state."""

    online: bool
    resolution: Resolution
    captured_fps: int
    desired_fps: int


class StreamerEncoder(_Base):
    """Streamer encoder configuration."""

    quality: int
    type: str


class SnapshotImage(_Base):
    """A JPEG taken from the video stream, with what ustreamer said about it.

    ``online`` is ``False`` when the frame is the "NO LIVE VIDEO" placeholder
    rather than a picture of the host, which is the only way to tell the two
    apart. A saved snapshot returned with ``load=True`` carries the same
    metadata it had when it was taken.

    Everything but ``data`` is optional: these come from response headers
    that no capture in this repository pins down, so a header that is absent
    or unreadable leaves its field unset rather than failing the call. With
    ``preview=True`` the size still describes the source frame, not the
    scaled-down ``data``.
    """

    data: bytes
    online: bool | None = None
    width: int | None = None
    height: int | None = None
    timestamp: float | None = None


class MJPEGFrame(_Base):
    """One frame of the MJPEG stream, with what its part headers said.

    ``data`` is a complete JPEG; the rest comes from the part headers, so a
    field is set only when the header was there and could be read. Without
    ``extra_headers=True`` only ``timestamp`` arrives — everything else is a
    ``X-UStreamer-*`` header ustreamer sends on request. ``headers`` keeps the
    raw part headers, including the timing ones this model does not name.

    Attributes:
        data: The JPEG bytes, empty when the stream was opened with
            ``zero_data=True``.
        timestamp: ``X-Timestamp``, a Unix time with microseconds.
        online: Whether the frame is a picture of the host rather than the
            "NO LIVE VIDEO" placeholder.
        width: Frame width in pixels.
        height: Frame height in pixels.
        dropped: How many frames ustreamer dropped for this client so far.
        client_fps: The rate ustreamer is sending this client.
        latency: Seconds between grabbing the frame and sending it.
        headers: Every part header, as received.
    """

    data: bytes
    timestamp: float | None = None
    online: bool | None = None
    width: int | None = None
    height: int | None = None
    dropped: int | None = None
    client_fps: int | None = None
    latency: float | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class StreamerH264(_Base):
    """H.264 encoder runtime state."""

    bitrate: int
    fps: int
    gop: int
    online: bool


class StreamerSinkInfo(_Base):
    """Sink (jpeg/h264) client status."""

    has_clients: bool


class StreamerSinks(_Base):
    """Streamer output sinks."""

    h264: StreamerSinkInfo
    jpeg: StreamerSinkInfo


class StreamerClientStat(_Base):
    """One MJPEG client of the streamer, as ustreamer accounts for it.

    Every field but ``fps`` echoes the query the client connected with, so a
    caller that passed a ``key`` to
    [`StreamerResource.mjpeg()`][aiopikvm.resources.streamer.StreamerResource.mjpeg]
    can find its own row: the id these are keyed by is ustreamer's, assigned
    at connect and not known to the client that owns it.

    Attributes:
        fps: Frames per second ustreamer is sending this client.
        key: The ``key`` query parameter it connected with, ``""`` if none.
        extra_headers: Whether it asked for the ``X-UStreamer-*`` part headers.
        advance_headers: Whether it asked for the Chromium workaround.
        dual_final_frames: Whether it asked for the Safari workaround.
        zero_data: Whether it asked for part headers without the JPEG data.
    """

    fps: int
    key: str = ""
    extra_headers: bool = False
    advance_headers: bool = False
    dual_final_frames: bool = False
    zero_data: bool = False


class StreamerStream(_Base):
    """Stream connection statistics."""

    clients: int
    clients_stat: dict[str, StreamerClientStat]
    queued_fps: int


class Streamer(_Base):
    """Running streamer process state.

    Present only when the streamer is active. ``StreamerState.streamer`` is
    ``None`` when no clients are subscribed and kvmd has shut the streamer
    process down. ``h264`` is absent unless ustreamer was built and configured
    with H.264 support.
    """

    encoder: StreamerEncoder
    instance_id: str
    sinks: StreamerSinks
    source: StreamerSource
    stream: StreamerStream
    h264: StreamerH264 | None = None


class StreamerLimitRange(_Base):
    """Numeric parameter range."""

    min: int
    max: int


class StreamerLimits(_Base):
    """Limits for the tunable streamer parameters.

    Only ``desired_fps`` is always present. kvmd adds the H.264 ranges only
    when H.264 is configured, and ``available_resolutions`` only on a device
    whose capture hardware can switch resolution.
    """

    desired_fps: StreamerLimitRange
    h264_bitrate: StreamerLimitRange | None = None
    h264_gop: StreamerLimitRange | None = None
    available_resolutions: list[str] | None = None


class StreamerFeatures(_Base):
    """Streamer feature flags."""

    h264: bool
    quality: bool
    resolution: bool


class StreamerParams(_Base):
    """Streamer parameters.

    Mirrors what the device supports: ``quality`` is absent when the capture
    path has no adjustable JPEG quality, ``resolution`` only exists on
    resolution-capable hardware, and the H.264 pair only when H.264 is
    configured. Used for both the requested parameters and the applied ones.
    """

    desired_fps: int
    quality: int | None = None
    resolution: str | None = None
    h264_bitrate: int | None = None
    h264_gop: int | None = None


class SavedSnapshot(_Base):
    """Metadata of the snapshot stored on the device.

    kvmd keeps the image itself out of the state and reports only what it
    was: whether the source was live and how big the frame is.
    """

    online: bool
    width: int
    height: int


class StreamerSnapshot(_Base):
    """The snapshot stored on the device, if any.

    ``saved`` is ``None`` until something calls
    [`StreamerResource.snapshot()`][aiopikvm.resources.streamer.StreamerResource.snapshot]
    with ``save=True``.
    """

    saved: SavedSnapshot | None = None


class StreamerState(_Base):
    """Streamer subsystem state.

    Mirrors the shape returned by ``GET /api/streamer``. The ``streamer``
    field is ``None`` when no stream clients are connected — kvmd stops the
    streamer process to save resources.
    """

    features: StreamerFeatures
    limits: StreamerLimits
    params: StreamerParams
    applied: StreamerParams
    snapshot: StreamerSnapshot
    streamer: Streamer | None = None


class OCRLangs(_Base):
    """Available and default OCR languages."""

    available: list[str]
    default: list[str]


class OCRInfo(_Base):
    """OCR capability metadata returned by ``GET /api/streamer/ocr``."""

    enabled: bool
    langs: OCRLangs
