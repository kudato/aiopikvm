"""Models for the Janus gateway and its ustreamer plugin.

Nothing here is a kvmd envelope. Janus has its own message format — a
``janus`` field naming the kind, a ``transaction`` on anything that answers a
request, and a plugin's own payload buried in ``plugindata.data`` — and the
ustreamer plugin has a second one inside that. The models follow both as they
are on the wire rather than flattening them, so that a message can be read
back against Janus's own documentation.
"""

from aiopikvm.models._base import _Base


class WebRTCICE(_Base):
    """The ICE server the plugin suggests.

    Attributes:
        url: A STUN or TURN URL, e.g. ``"stun:stun.l.google.com:19302"``. It
            is whatever ``JANUS_USTREAMER_WEB_ICE_URL`` was set to on the
            device, falling back to the plugin's compiled-in default, and it
            can name a host on the public internet. The client does not use it
            unless it is asked to — see the ``ice_servers`` argument of
            [`PiKVM.webrtc()`][aiopikvm.PiKVM.webrtc]. ``None`` when the
            plugin was built without one.
    """

    url: str | None = None


class WebRTCFeatures(_Base):
    """What this build of the ustreamer plugin can do.

    Attributes:
        audio: Whether the device has a capture audio device, i.e. whether the
            host's sound can be streamed to the client.
        mic: Whether the device has a playback audio device, i.e. whether the
            client's microphone can be sent to the host.
        ice: The ICE server the plugin suggests.
    """

    audio: bool = False
    mic: bool = False
    ice: WebRTCICE = WebRTCICE()


class WebRTCResult(_Base):
    """The plugin's answer to a request that succeeded.

    The plugin names the status and then repeats it as the key its payload
    hangs off, so a ``features`` answer is ``{"status": "features",
    "features": {...}}`` and a ``started`` answer is ``{"status": "started"}``
    with nothing beside it.

    Attributes:
        status: ``"started"`` after ``watch`` and after ``start``,
            ``"stopped"`` after ``stop``, ``"features"`` after ``features``.
        features: The payload of a ``features`` answer, ``None`` otherwise.
    """

    status: str
    features: WebRTCFeatures | None = None


class WebRTCPluginEvent(_Base):
    """What the ustreamer plugin itself said.

    Either a result or an error, never both. An error here is a *plugin*
    error and rides inside a message Janus considers successful — Janus's own
    errors are a different shape, at the top level of the message.

    Attributes:
        ustreamer: Always ``"event"``; the plugin stamps every message it
            pushes with it.
        result: The answer, when the request succeeded.
        error_code: The plugin's own code — 400 for a body with no ``request``
            or one whose ``request`` is not a string, 405 for a request name
            it does not implement. ``None`` when the request succeeded.
        error: The text beside that code, e.g. ``"Not implemented"``.
    """

    ustreamer: str = "event"
    result: WebRTCResult | None = None
    error_code: int | None = None
    error: str | None = None


class WebRTCPluginData(_Base):
    """One plugin payload, as Janus wraps it.

    Attributes:
        plugin: The plugin package, ``"janus.plugin.ustreamer"`` for
            everything this client sends.
        data: What the plugin itself said.
    """

    plugin: str
    data: WebRTCPluginEvent


class WebRTCJSEP(_Base):
    """An SDP, as Janus carries one.

    Only one message in a session has it: the plugin's answer to ``watch``,
    which is the offer this client owes an answer to.

    Attributes:
        type: ``"offer"`` on everything the device sends; ``"answer"`` is what
            this client sends back inside ``start``.
        sdp: The session description itself.
    """

    type: str
    sdp: str


class WebRTCEvent(_Base):
    """One message Janus sent that answers nothing this client asked for.

    These arrive whenever Janus has something to say: the peer connection came
    up, the link is congested, the peer connection ended, the session timed
    out. The plugin's own pushes arrive the same way, since it answers a
    request by pushing an event rather than by replying to it, which is why
    ``plugindata`` can be set on one of these.

    An answer to something this client *did* send never arrives here — it is
    consumed where the request was made — so the ``transaction`` such an
    answer carries, and the ``error`` block a refusal carries, have no field
    here. A Janus-level refusal reaches a caller as
    [`WebRTCError`][aiopikvm.WebRTCError] instead.

    Attributes:
        janus: The kind — ``"webrtcup"``, ``"slowlink"``, ``"hangup"``,
            ``"timeout"``, ``"event"``, ``"media"``, and whatever a newer
            Janus adds.
        sender: The handle the message concerns. ``None`` on a message about
            the session as a whole.
        session_id: The session the message concerns.
        plugindata: The plugin's payload on an ``"event"``, ``None`` on
            anything else.
        jsep: The SDP beside a plugin push, which is how the offer arrives.
        type: On ``"media"``, which kind of media it is about — ``"video"`` or
            ``"audio"``.
        mid: On ``"media"``, the media identifier from the SDP — ``"v"`` for
            the ustreamer plugin's video, ``"a"`` for its audio.
        receiving: On ``"media"``, whether packets of that kind are arriving.
            Janus events this for the media it *receives*, so a session that
            only watches never sees one.
        uplink: On ``"slowlink"``, whether the congested direction is the one
            towards Janus.
        lost: On ``"slowlink"``, how many packets went missing.
        reason: On ``"hangup"``, why the peer connection ended.
    """

    janus: str
    sender: int | None = None
    session_id: int | None = None
    plugindata: WebRTCPluginData | None = None
    jsep: WebRTCJSEP | None = None
    type: str | None = None
    mid: str | None = None
    receiving: bool | None = None
    uplink: bool | None = None
    lost: int | None = None
    reason: str | None = None
