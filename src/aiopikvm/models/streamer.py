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
    process down.
    """

    encoder: StreamerEncoder
    h264: StreamerH264
    instance_id: str
    sinks: StreamerSinks
    source: StreamerSource
    stream: StreamerStream


class StreamerLimitRange(_Base):
    """Numeric parameter range."""

    min: int
    max: int


class StreamerLimits(_Base):
    """Limits for tunable streamer parameters."""

    desired_fps: StreamerLimitRange
    h264_bitrate: StreamerLimitRange
    h264_gop: StreamerLimitRange


class StreamerFeatures(_Base):
    """Streamer feature flags."""

    h264: bool
    quality: bool
    resolution: bool


class StreamerParams(_Base):
    """Current streamer parameters."""

    desired_fps: int
    h264_bitrate: int
    h264_gop: int
    quality: int


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
    snapshot: StreamerSnapshot
    streamer: Streamer | None = None


class OCRResult(_Base):
    """OCR recognition result."""

    ocr: str
