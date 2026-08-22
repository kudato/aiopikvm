"""Models for the kvmd-media daemon — what it offers and what it sends."""

from aiopikvm.models._base import _Base


class MediaH264(_Base):
    """What the daemon says about its H.264 source.

    Attributes:
        profile_level_id: The SDP ``profile-level-id`` of the stream, e.g.
            ``"42E01F"`` for constrained baseline at level 3.1. A WebRTC or
            SDP consumer needs it to describe the track it is about to
            receive; a caller that only wants the frames can ignore it.
    """

    profile_level_id: str


class MediaJPEG(_Base):
    """What the daemon says about its JPEG source.

    Nothing, on every kvmd this release was checked against: the daemon has
    no metadata to publish for MJPEG the way it does for H.264. The model
    exists so that the format shows up as present rather than as an unnamed
    extra when a device serves it.
    """


class MediaVideoFormats(_Base):
    """The video formats the daemon is configured with.

    A format the daemon does not serve is simply absent, which is what makes
    both fields optional: a PiKVM v3 with H.264 offloaded to the hardware
    encoder publishes ``h264`` and nothing else, and asking a
    [`MediaWebSocket`][aiopikvm.MediaWebSocket] for a format that is not here
    is refused with HTTP 400 before the socket exists.

    Attributes:
        h264: H.264 metadata, ``None`` when the daemon has no H.264 source.
        jpeg: MJPEG metadata, ``None`` when the daemon has no JPEG source.
    """

    h264: MediaH264 | None = None
    jpeg: MediaJPEG | None = None


class MediaState(_Base):
    """What the kvmd-media daemon offers, as ``GET /api/media`` returns it.

    The same object arrives as the first frame of a
    [`MediaWebSocket`][aiopikvm.MediaWebSocket] opened without a format, where
    it is on [`MediaWebSocket.media`][aiopikvm.MediaWebSocket.media].

    Attributes:
        video: The video formats the daemon can send.
    """

    video: MediaVideoFormats


class MediaFrame(_Base):
    """One frame off the media socket.

    Attributes:
        data: The frame as the daemon sent it. For H.264 that is Annex B —
            a ``00 00 00 01`` start code, then the NAL header — and one
            message can carry several NAL units, an SPS and a PPS ahead of
            the keyframe they describe.
        key: Whether this frame is a keyframe. ``None`` on a socket opened
            with a format, which sends the frame and nothing else; the flag
            only exists in the operation the format-less socket uses.
    """

    data: bytes
    key: bool | None = None
