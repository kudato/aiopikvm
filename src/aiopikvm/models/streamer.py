"""Streamer models."""

from typing import Any

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
    apart. The metadata is missing when the snapshot came from the cache
    (``load=True``) rather than from the running streamer.
    """

    data: bytes
    online: bool | None = None
    width: int | None = None
    height: int | None = None
    timestamp: float | None = None


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


class StreamerStream(_Base):
    """Stream connection statistics."""

    clients: int
    clients_stat: dict[str, Any]
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


class StreamerSnapshot(_Base):
    """Cached snapshot info."""

    saved: dict[str, Any] | None = None


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
