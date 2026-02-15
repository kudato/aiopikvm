"""Streamer models."""

from aiopikvm.models._base import _Base


class Resolution(_Base):
    """Video resolution."""

    width: int
    height: int


class StreamerSource(_Base):
    """Streamer video source state."""

    online: bool
    resolution: Resolution | None = None


class StreamerState(_Base):
    """Streamer subsystem state."""

    enabled: bool
    source: StreamerSource


class OCRResult(_Base):
    """OCR recognition result."""

    ocr: str
