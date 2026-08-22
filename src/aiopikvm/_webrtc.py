"""WebRTC video from PiKVM's Janus gateway.

PiKVM runs Janus behind ``/janus/ws`` with ustreamer's own plugin loaded into
it, and that is the path its web UI takes by default — of the three video
surfaces a standard install exposes it is the one with the lowest latency,
which is what matters to a human moving a mouse. The other two,
[`MediaWebSocket`][aiopikvm.MediaWebSocket] and ustreamer's MJPEG, are the
better fit for a program that reads frames, and they cost nothing extra to
install.

Nothing here speaks kvmd. Janus has its own protocol: a session, a handle
attached to a plugin, and messages tagged with a transaction. A request is
answered twice — Janus acknowledges it straight away, and the plugin's own
answer arrives afterwards as a separate event, since the plugin *pushes*
rather than replies. This client keeps the two apart: transactions match up
Janus's acknowledgements, and every plugin push is routed by the handle it
came from.

The negotiation itself is inverted from the usual. The ustreamer plugin is
the offerer: ``watch`` comes back with an SDP offer and the client owes it an
answer, which it sends inside ``start``.

Needs the ``webrtc`` extra — ``pip install 'aiopikvm[webrtc]'`` — which pulls
aiortc and, with it, a bundled FFmpeg. Nothing else in this library imports
it, and neither does this module until a session is actually opened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

import websockets
import websockets.asyncio.client
from pydantic import ValidationError
from websockets.typing import Subprotocol

from aiopikvm._constants import AuthMode
from aiopikvm._exceptions import (
    ConfigurationError,
    ResponseError,
    WebRTCError,
    WebSocketError,
)
from aiopikvm._tls import CertTypes, VerifyTypes, build_ssl_context
from aiopikvm._ws import _Connector, _credential_headers, _handshake_error, _ws_url
from aiopikvm.models.webrtc import WebRTCEvent, WebRTCFeatures, WebRTCPluginEvent

if TYPE_CHECKING:
    from aiortc import MediaStreamTrack
    from aiortc.rtcpeerconnection import RTCPeerConnection
    from av.audio.frame import AudioFrame
    from av.frame import Frame
    from av.packet import Packet
    from av.video.frame import VideoFrame

logger = logging.getLogger(__name__)

type _Failure = WebSocketError | WebRTCError | None
"""What the signalling reader can stop on, or ``None`` while it has not."""

_JANUS_PATH = "/janus/ws"
_SUBPROTOCOL = Subprotocol("janus-protocol")
"""Janus serves its WebSocket transport under this subprotocol and no other.

A handshake that does not ask for it is refused before any message is sent.
"""

_PLUGIN = "janus.plugin.ustreamer"
"""The plugin package ustreamer registers itself under (``janus/src/const.h``)."""

_KEEPALIVE_INTERVAL = 25.0
"""Seconds between session keepalives.

Janus drops a session that has said nothing for sixty seconds, and dropping it
tears down the peer connection with it. kvmd's own web UI uses the same
interval.
"""

_FRAME_BUFFER = 8
"""How many decoded frames to hold per track before the oldest is dropped.

aiortc queues decoded frames without a limit, so a consumer that stalls turns
into memory that grows. This is live video: the useful frame is the newest
one, and an old one nobody read is worth less than the memory it occupies.
Pass a large *frame_buffer* to
[`PiKVM.webrtc()`][aiopikvm.PiKVM.webrtc] if the recording matters more than
the latency — but for recording, [`PiKVM.media_ws()`][aiopikvm.PiKVM.media_ws]
hands over the encoded stream and needs no decoder at all.
"""

_EVENT_BUFFER = 256
"""How many Janus events to hold before the oldest is dropped.

These are lifecycle notifications, a handful per session; a caller that never
reads [`events()`][aiopikvm.WebRTCSession.events] is the normal case and must
not accumulate.
"""


class _Finished(Exception):
    """Internal signal: the socket closed and the reader is done."""


class WebRTCSession:
    """A live WebRTC session against PiKVM's Janus gateway.

    Usage:

        async with kvm.webrtc() as rtc:
            async for frame in rtc.video():
                image = frame.to_ndarray(format="bgr24")

    Entering the block negotiates the whole thing — session, handle, feature
    query, offer, answer, DTLS — and returns once Janus reports the peer
    connection up, so a block that starts running has video on the way.
    Leaving it stops the stream and destroys the session, whatever happened
    inside.

    Like [`MediaWebSocket`][aiopikvm.MediaWebSocket], this needs a
    [`PiKVM.ws()`][aiopikvm.PiKVM.ws] held open beside it. kvmd runs ustreamer
    only while a session has asked to be counted as a viewer, and the plugin
    reads its frames out of ustreamer — so without one the negotiation
    succeeds in every visible way, Janus reports the peer connection up, and
    no frame ever arrives.

    The frames it yields are decoded — aiortc runs the H.264 decoder — which
    is the difference from the media socket, where the frames arrive encoded
    and a decoder is the caller's problem.
    """

    def __init__(
        self,
        url: str,
        *,
        user: str,
        passwd: str | Callable[[], str],
        auth: AuthMode = "headers",
        token: str = "",
        verify_ssl: VerifyTypes = True,
        cert: CertTypes | None = None,
        proxy: str | None = None,
        trust_env: bool = True,
        audio: bool = False,
        orientation: int = 0,
        ice_servers: Sequence[str] | None = None,
        frame_buffer: int = _FRAME_BUFFER,
        keepalive_interval: float = _KEEPALIVE_INTERVAL,
        follow_redirects: bool = False,
        open_timeout: float = 10.0,
        close_timeout: float = 10.0,
        negotiate_timeout: float = 30.0,
        ping_interval: float | None = 20.0,
        ping_timeout: float | None = 20.0,
    ) -> None:
        """Prepare a session.

        Args:
            url: PiKVM base URL, ``https://`` or ``http://``.
            user: kvmd user name.
            passwd: Password, TOTP code appended if the device asks for one.
                A zero-argument callable is called when the handshake is made,
                so a rotating code is the one current then.
            auth: Which credential the handshake carries; ``"cookie"`` needs
                *token* and ignores *user* and *passwd*.
            token: Session token for ``auth="cookie"``.
            verify_ssl: What to trust; see
                [`VerifyTypes`][aiopikvm.VerifyTypes].
            cert: Client certificate to present.
            proxy: Proxy URL to reach the device through. ``None`` leaves it
                to the environment, unless *trust_env* says otherwise. It
                covers the signalling only — media travels over UDP and never
                goes near it.
            trust_env: Read the proxy configuration from the environment.
            audio: Ask for the host's audio alongside the video. The device
                needs a capture device for it;
                [`features`][aiopikvm.WebRTCSession.features] says whether it
                has one, and asking without one simply yields no audio track.
            orientation: Rotate the video, ``0``, ``90``, ``180`` or ``270``.
                The plugin rotates anything else to ``0``.
            ice_servers: STUN or TURN URLs to gather candidates through.
                ``None``, the default, uses none: a PiKVM is on the same
                network as whatever is talking to it, host candidates are
                enough, and a STUN server is a third party this client will
                not contact uninvited. The device suggests one of its own on
                [`features`][aiopikvm.WebRTCSession.features].
            frame_buffer: How many decoded frames to hold per track before the
                oldest is dropped.
            keepalive_interval: Seconds between session keepalives. Janus
                drops a session silent for sixty.
            follow_redirects: Follow a redirected handshake instead of raising
                [`RedirectError`][aiopikvm.RedirectError]. Off by default: the
                upgrade carries the password in a header.
            open_timeout: Seconds to wait for the handshake, and for each
                individual Janus message to be acknowledged.
            close_timeout: Seconds to wait for the closing handshake.
            negotiate_timeout: Seconds to allow the whole negotiation, from
                the session being created to Janus reporting the peer
                connection up.
            ping_interval: Seconds between *websockets*' own keepalive pings
                on the signalling socket, ``None`` to send none. This is the
                WebSocket protocol's keepalive, not Janus's.
            ping_timeout: Seconds to wait for a keepalive pong before
                declaring the signalling link dead, ``None`` to wait forever.

        Raises:
            ConfigurationError: If the URL scheme is not ``https`` or ``http``.
        """
        self._url = f"{_ws_url(url)}{_JANUS_PATH}"
        self._user = user
        self._passwd = passwd
        self._auth = auth
        self._token = token
        self._verify_ssl = verify_ssl
        self._cert = cert
        self._proxy = proxy
        self._trust_env = trust_env
        self._audio = audio
        self._orientation = orientation
        self._ice_servers = list(ice_servers or ())
        self._frame_buffer = frame_buffer
        self._keepalive_interval = keepalive_interval
        self._follow_redirects = follow_redirects
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._negotiate_timeout = negotiate_timeout
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout

        self._connection: websockets.asyncio.client.ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._keeper: asyncio.Task[None] | None = None
        self._pumps: dict[str, asyncio.Task[None]] = {}
        self._pc: RTCPeerConnection | None = None
        self._tracks: dict[str, MediaStreamTrack] = {}
        self._buffers: dict[str, deque[Frame | Packet[Any]]] = {}
        self._wakeups: dict[str, asyncio.Event] = {}
        self._counter = 0
        self._acks: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pushes: asyncio.Queue[tuple[WebRTCPluginEvent, dict[str, Any] | None]] = (
            asyncio.Queue()
        )
        self._events: deque[WebRTCEvent] = deque()
        self._event_wakeup = asyncio.Event()
        self._up = asyncio.Event()
        self._failure: _Failure = None
        self._reported = False
        self._session_id: int | None = None
        self._handle_id: int | None = None
        self._features: WebRTCFeatures | None = None

    # --- What the session knows ----------------------------------------

    @property
    def features(self) -> WebRTCFeatures | None:
        """What the plugin said it can do, read while the session opened.

        ``None`` before the session is entered.
        """
        return self._features

    @property
    def session_id(self) -> int | None:
        """Janus's id for this session, ``None`` before it is created."""
        return self._session_id

    @property
    def handle_id(self) -> int | None:
        """Janus's id for the plugin handle, ``None`` before it is attached."""
        return self._handle_id

    def track(self, kind: str = "video") -> MediaStreamTrack | None:
        """The aiortc track of a kind, for a caller that wants it directly.

        Handing this to aiortc's own ``MediaRecorder`` or ``MediaRelay`` is
        what it is for. Note that the session is already pulling frames off
        it into its own buffer, so a second consumer will see only what the
        first does not take first — use one or the other, not both.

        Args:
            kind: ``"video"`` or ``"audio"``.

        Returns:
            The track, or ``None`` when the session has none of that kind.
        """
        return self._tracks.get(kind)

    # --- The block ------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Open the socket and negotiate the whole session.

        Returns:
            This session, with video on the way.

        Raises:
            ConfigurationError: The ``webrtc`` extra is not installed.
            AuthError: kvmd refused the credentials during the upgrade — 401
                when none reached it, 403 when the ones that did were
                rejected.
            RedirectError: The upgrade was redirected and *follow_redirects*
                is off. Following it would resend the password to the target.
            APIError: The upgrade was rejected for another reason.
            ResponseError: Janus or the plugin answered with a shape this
                release cannot read.
            WebRTCError: Janus or the plugin refused, or the negotiation did
                not finish within *negotiate_timeout*.
            WebSocketError: The signalling connection could not be
                established, or it broke during the negotiation.
        """
        peer_connection = _peer_connection(self._ice_servers)
        await self._connect()
        try:
            async with asyncio.timeout(self._negotiate_timeout):
                await self._negotiate(peer_connection)
        except BaseException as exc:
            # __aexit__ never runs for a failed __aenter__, and by now there
            # is a session on the device that nothing else will destroy. It
            # is told what went wrong rather than being handed three Nones:
            # a teardown that thinks the block ended cleanly raises the
            # reader's recorded failure over this one, and for a cancellation
            # that means the cancellation is swallowed and replaced.
            await self.__aexit__(type(exc), exc, exc.__traceback__)
            if isinstance(exc, TimeoutError):
                # asyncio.timeout turns its own expiry into this, and only
                # its own: an outer cancellation still arrives as one.
                raise WebRTCError(
                    "Janus did not bring the peer connection up within "
                    f"{self._negotiate_timeout} s"
                ) from exc
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the stream and tear the session down, whatever happened.

        Args:
            exc_type: Type of the exception the block raised, if any.
            exc_val: The exception the block raised, if any.
            exc_tb: Traceback of that exception, if any.

        Raises:
            WebRTCError: The signalling broke while nothing was looking and
                the block itself ended cleanly.
            WebSocketError: Likewise, when the socket itself was what broke.
        """
        for pump in self._pumps.values():
            pump.cancel()
        for pump in self._pumps.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
        self._pumps.clear()

        await self._farewell()
        await self._stop(self._keeper)
        self._keeper = None
        if self._pc is not None:
            with contextlib.suppress(Exception):
                await self._pc.close()
            self._pc = None
        await self._stop(self._reader)
        self._reader = None
        if self._connection is not None:
            try:
                await self._connection.close()
            finally:
                self._connection = None
        self._fail_acks("The signalling socket closed before Janus answered")

        if exc_type is None and self._failure is not None and not self._reported:
            self._reported = True
            raise self._failure

    # --- Reading it -----------------------------------------------------

    async def video(self) -> AsyncIterator[VideoFrame]:
        """Iterate over the decoded video frames as they arrive.

        Frames are already through the H.264 decoder — they are PyAV
        ``VideoFrame`` objects, so ``frame.to_ndarray(format="bgr24")`` or
        ``frame.to_image()`` is one call away.

        A consumer that falls behind loses the frames it did not reach: the
        buffer holds *frame_buffer* of them and the oldest goes when a new one
        arrives. That is deliberate for live video, where the newest frame is
        the only one worth having.

        The iteration ends when the track does — the session being closed, the
        stream being stopped from the other side.

        Yields:
            Each video frame Janus sent.

        Raises:
            WebRTCError: The session is not open.
        """
        from av.video.frame import VideoFrame

        async for frame in self._frames("video"):
            if isinstance(frame, VideoFrame):
                yield frame

    async def audio(self) -> AsyncIterator[AudioFrame]:
        """Iterate over the decoded audio frames as they arrive.

        Only useful on a session opened with ``audio=True`` against a device
        that has a capture device; anywhere else there is no audio track and
        this yields nothing at all.

        Yields:
            Each audio frame Janus sent.

        Raises:
            WebRTCError: The session is not open.
        """
        from av.audio.frame import AudioFrame

        async for frame in self._frames("audio"):
            if isinstance(frame, AudioFrame):
                yield frame

    async def events(self) -> AsyncIterator[WebRTCEvent]:
        """Iterate over what Janus says about the session as it runs.

        The peer connection coming up, the link going congested, the peer
        connection ending, the session timing out, and the plugin's own
        pushes. None of these are answers to anything this client sent — a
        request's acknowledgement is consumed where the request was made.

        Janus's ``media`` event is not among them, whatever the Janus
        documentation suggests: it reports the media Janus *receives*, and a
        session that only watches sends none. The first frame is the only
        thing that says video started.

        Nothing here has to be read: the buffer holds a few hundred and drops
        the oldest beyond that, so ignoring it costs nothing.

        Yields:
            Each event, oldest first.

        Raises:
            WebRTCError: The session is not open.
            WebSocketError: The signalling connection broke.
        """
        self._ensure_open()
        while True:
            while self._events:
                yield self._events.popleft()
            reader = self._reader
            if reader is None or reader.done():
                self._raise_failure()
                return
            self._event_wakeup.clear()
            if self._events:
                continue
            await self._event_wakeup.wait()

    # --- Asking for things ----------------------------------------------

    async def request_keyframe(self) -> None:
        """Ask the encoder for a keyframe now.

        Useful after a decoder has lost its reference, or to shorten the wait
        when a stream with a long group of pictures is joined. There is no
        acknowledgement from the plugin — it sets a flag and says nothing —
        so the keyframe simply turns up as the next frame that decodes on its
        own.

        Raises:
            WebRTCError: The session is not open, or Janus refused the
                message.
            WebSocketError: The signalling connection broke.
        """
        with self._told_the_caller():
            await self._plugin_request("key_required")

    async def keepalive(self) -> None:
        """Send one session keepalive now.

        The session sends these on its own every *keepalive_interval*
        seconds; this is here for a caller who suspended the event loop and
        wants to prove the session survived it.

        Raises:
            WebRTCError: The session is not open, or Janus no longer knows it.
            WebSocketError: The signalling connection broke.
        """
        self._ensure_open()
        with self._told_the_caller():
            await self._request(janus="keepalive", session_id=self._session_id)

    # --- Negotiation ----------------------------------------------------

    async def _connect(self) -> None:
        """Open the signalling socket and start reading it.

        Raises:
            AuthError: kvmd refused the credentials during the upgrade.
            RedirectError: The upgrade was redirected and following is off.
            APIError: The upgrade was rejected for another reason.
            WebSocketError: The connection could not be established.
        """
        # `ws://` carries no TLS, so there is nothing to configure there.
        ssl_context: ssl.SSLContext | None = None
        if self._url.startswith("wss://"):
            ssl_context = build_ssl_context(self._verify_ssl, self._cert)

        try:
            self._connection = await _Connector(
                self._url,
                additional_headers=_credential_headers(
                    self._auth, self._user, self._passwd, self._token
                ),
                ssl_context=ssl_context,
                proxy=(self._proxy or (True if self._trust_env else None)),
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                follow_redirects=self._follow_redirects,
                subprotocols=[_SUBPROTOCOL],
            )
        except websockets.exceptions.InvalidStatus as exc:
            # The upgrade never happened: kvmd's auth sits in front of Janus,
            # so this is an ordinary HTTP refusal, kvmd envelope and all.
            raise _handshake_error(exc.response) from exc
        except (
            OSError,
            ValueError,
            websockets.exceptions.WebSocketException,
        ) as exc:
            raise WebSocketError(
                f"Failed to connect to the Janus gateway: {exc}"
            ) from exc

        self._counter = 0
        while not self._pushes.empty():
            self._pushes.get_nowait()
        self._events.clear()
        self._event_wakeup.clear()
        self._up.clear()
        self._failure = None
        self._reported = False
        self._reader = asyncio.create_task(self._drain())

    async def _negotiate(
        self, peer_connection: Callable[[], RTCPeerConnection]
    ) -> None:
        """Walk the whole Janus handshake, from ``create`` to ``webrtcup``.

        Args:
            peer_connection: Builds the aiortc peer connection. Taken as a
                callable so that the aiortc import happens before the socket
                is opened, and a missing extra costs the device nothing.

        Raises:
            ResponseError: Janus answered with a shape this release cannot
                read.
            WebRTCError: Janus or the plugin refused.
            WebSocketError: The signalling connection broke.
        """
        answer = await self._request(janus="create")
        self._session_id = _identifier(answer, "session")
        self._keeper = asyncio.create_task(self._keepalive())

        answer = await self._request(
            janus="attach", session_id=self._session_id, plugin=_PLUGIN
        )
        self._handle_id = _identifier(answer, "handle")

        await self._plugin_request("features")
        push, _ = await self._push()
        self._features = _features(push)

        self._pc = peer_connection()
        self._pc.on("track", self._on_track)

        await self._plugin_request(
            "watch",
            params={
                "orientation": self._orientation,
                "audio": self._audio,
                "mic": False,
                "camera": False,
            },
        )
        push, jsep = await self._push()
        offer = _offer(push, jsep)

        from aiortc import RTCSessionDescription

        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=offer, type="offer")
        )
        local = await self._pc.createAnswer()
        if local is None:  # pragma: no cover - aiortc only returns None with no m-lines
            raise WebRTCError(
                "aiortc had nothing to answer the plugin's offer with, which "
                "means the offer described no media at all"
            )
        await self._pc.setLocalDescription(local)

        await self._plugin_request(
            "start", jsep={"type": "answer", "sdp": self._pc.localDescription.sdp}
        )
        await self._push()

        # This client gathers every candidate before it answers, so the answer
        # above is already complete and the only trickle it owes is the one
        # that says so.
        await self._request(
            janus="trickle",
            session_id=self._session_id,
            handle_id=self._handle_id,
            candidate={"completed": True},
        )
        await self._up.wait()
        # The reader sets the same event on its way out, so a socket that
        # broke mid-negotiation arrives here looking exactly like success.
        self._raise_failure()

    def _on_track(self, track: MediaStreamTrack) -> None:
        """Start buffering a track Janus has just added.

        Args:
            track: The track aiortc built for it.
        """
        kind = track.kind
        self._tracks[kind] = track
        self._buffers[kind] = deque(maxlen=max(1, self._frame_buffer))
        self._wakeups[kind] = asyncio.Event()
        self._pumps[kind] = asyncio.create_task(self._pump(kind, track))

    async def _pump(self, kind: str, track: MediaStreamTrack) -> None:
        """Pull decoded frames off a track into its buffer, forever.

        Args:
            kind: ``"video"`` or ``"audio"``.
            track: The track to read.
        """
        from aiortc.mediastreams import MediaStreamError

        buffer = self._buffers[kind]
        wakeup = self._wakeups[kind]
        try:
            while True:
                frame = await track.recv()
                if buffer.maxlen is not None and len(buffer) == buffer.maxlen:
                    logger.debug("Dropping the oldest buffered %s frame", kind)
                buffer.append(frame)
                wakeup.set()
        except MediaStreamError:
            pass
        except Exception as exc:  # The track died in a way aiortc does not name.
            logger.warning("The %s track stopped: %s", kind, exc)
        finally:
            wakeup.set()

    async def _frames(self, kind: str) -> AsyncIterator[Frame | Packet[Any]]:
        """Hand over one track's buffered frames as they arrive.

        Args:
            kind: ``"video"`` or ``"audio"``.

        Yields:
            Each frame, oldest first.

        Raises:
            WebRTCError: The session is not open.
        """
        self._ensure_open()
        while True:
            buffer = self._buffers.get(kind)
            pump = self._pumps.get(kind)
            if buffer is None or pump is None:
                # Janus adds a track when it has one; a session that asked for
                # audio a device does not have never gets that one.
                return
            while buffer:
                yield buffer.popleft()
            if pump.done():
                return
            wakeup = self._wakeups[kind]
            wakeup.clear()
            if buffer:
                continue
            await wakeup.wait()

    # --- Talking to Janus -----------------------------------------------

    async def _request(self, **body: Any) -> dict[str, Any]:
        """Send one Janus message and wait for it to be acknowledged.

        Args:
            **body: The message, minus the transaction this adds.

        Returns:
            The message Janus answered with.

        Raises:
            WebRTCError: Janus refused, or it said nothing within
                *open_timeout*.
            WebSocketError: The client is not connected, the reader that
                resolves acknowledgements is no longer running, or the
                connection broke before the message could be sent.
        """
        if self._reader is None or self._reader.done():
            # Nothing is left to match an acknowledgement to its transaction,
            # so the wait below could only end in the timeout — and that
            # message names the request, not the reason. It is raised rather
            # than handed to _raise_failure(), which would mark it reported:
            # the teardown sends through here and swallows what comes back,
            # and __aexit__ would then have nothing left to say about a link
            # that died with nobody looking.
            if self._failure is not None:
                raise self._failure
            raise WebSocketError(
                "The Janus signalling reader is not running, so nothing can "
                "be acknowledged"
            )
        self._counter += 1
        transaction = f"aiopikvm-{self._counter}"
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._acks[transaction] = future
        try:
            await self._send({**body, "transaction": transaction})
            async with asyncio.timeout(self._open_timeout):
                answer = await future
        except TimeoutError as exc:
            raise WebRTCError(
                f"Janus did not answer {body.get('janus')!r} within "
                f"{self._open_timeout} s"
            ) from exc
        finally:
            self._acks.pop(transaction, None)

        if answer.get("janus") == "error":
            error = answer.get("error")
            error = error if isinstance(error, dict) else {}
            code = error.get("code")
            reason = error.get("reason")
            raise WebRTCError(
                f"Janus refused {body.get('janus')!r}: {reason or 'no reason given'}",
                code if isinstance(code, int) else 0,
                reason=reason if isinstance(reason, str) else "",
            )
        return answer

    async def _plugin_request(
        self,
        request: str,
        *,
        params: dict[str, Any] | None = None,
        jsep: dict[str, str] | None = None,
    ) -> None:
        """Send one message to the ustreamer plugin.

        The plugin's own answer is not here: it pushes an event instead of
        replying, so [`_push()`][aiopikvm._webrtc.WebRTCSession._push] is
        where it turns up. Only ``key_required`` has none to wait for.

        Args:
            request: The request name, e.g. ``"watch"``.
            params: The ``params`` object, for the requests that take one.
            jsep: The SDP to carry alongside it.

        Raises:
            WebRTCError: The session is not open, or Janus refused the
                message.
            WebSocketError: The connection broke before it could be sent.
        """
        self._ensure_open()
        body: dict[str, Any] = {"request": request}
        if params is not None:
            body["params"] = params
        message: dict[str, Any] = {
            "janus": "message",
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "body": body,
        }
        if jsep is not None:
            message["jsep"] = jsep
        await self._request(**message)

    async def _push(self) -> tuple[WebRTCPluginEvent, dict[str, Any] | None]:
        """Wait for the plugin's next push.

        Returns:
            What the plugin said, and the SDP beside it when there was one.

        Raises:
            WebRTCError: The plugin refused, or it said nothing within
                *open_timeout*.
        """
        try:
            async with asyncio.timeout(self._open_timeout):
                push, jsep = await self._pushes.get()
        except TimeoutError as exc:
            raise WebRTCError(
                f"The ustreamer plugin said nothing within {self._open_timeout} s"
            ) from exc
        if push.error_code is not None:
            raise WebRTCError(
                f"The ustreamer plugin refused: {push.error or 'no reason given'}",
                push.error_code,
                reason=push.error or "",
            )
        return push, jsep

    async def _send(self, message: dict[str, Any]) -> None:
        """Put one message on the signalling socket.

        Args:
            message: The message to send.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before it could be sent.
        """
        if self._connection is None:
            raise WebSocketError("Not connected to the Janus gateway")
        try:
            await self._connection.send(json.dumps(message))
        except websockets.exceptions.WebSocketException as exc:
            raise WebSocketError(
                f"Failed to send {message.get('janus')!r} to Janus: {exc}"
            ) from exc

    async def _keepalive(self) -> None:
        """Keep the Janus session alive until the block ends."""
        while True:
            await asyncio.sleep(self._keepalive_interval)
            try:
                await self._request(janus="keepalive", session_id=self._session_id)
            except (WebRTCError, WebSocketError) as exc:
                # Janus has forgotten the session, or the socket is gone.
                # Either way the video is over and there is nobody to tell.
                logger.warning("The Janus keepalive stopped: %s", exc)
                if self._failure is None:
                    self._failure = exc
                return

    async def _farewell(self) -> None:
        """Stop the stream and destroy the session, on a best-effort basis.

        Every step here is one the device would sort out for itself — a
        session with nothing keeping it alive is dropped after a minute — so
        a failure is logged and swallowed rather than raised over whatever
        the block was already doing.

        Each step is independent and each is guarded on its own, so one that
        fails does not take the rest with it. ``destroy`` is the step that
        actually frees the session, and giving up before it is what leaves
        one on the device for Janus's sixty-second silence timeout to reap.
        """
        if self._connection is None or self._session_id is None:
            return
        steps: list[dict[str, Any]] = []
        if self._handle_id is not None:
            steps.append(
                {
                    "janus": "message",
                    "session_id": self._session_id,
                    "handle_id": self._handle_id,
                    "body": {"request": "stop"},
                }
            )
            steps.append(
                {
                    "janus": "detach",
                    "session_id": self._session_id,
                    "handle_id": self._handle_id,
                }
            )
        steps.append({"janus": "destroy", "session_id": self._session_id})
        for message in steps:
            try:
                await self._request(**message)
            except (WebRTCError, WebSocketError) as exc:
                logger.debug(
                    "Could not %s the Janus session: %s", message["janus"], exc
                )
                continue
        self._handle_id = None
        self._session_id = None

    # --- Reading the socket ---------------------------------------------

    async def _drain(self) -> None:
        """Read the signalling socket and route every message it carries."""
        try:
            while True:
                self._route(await self._read())
        except _Finished:
            if not self._up.is_set() and self._failure is None:
                # A clean close is how a session ends, but not how one starts.
                # The `finally` below sets `_up` so that a dead reader cannot
                # hang the negotiation, which leaves the two indistinguishable
                # from there — so the difference is recorded here, while the
                # event still means what it says. The window is between the
                # last trickle acknowledgement and `webrtcup`; a Janus or
                # nginx restart is what lands in it.
                self._failure = WebSocketError(
                    "The Janus signalling connection closed before the peer "
                    "connection came up"
                )
        except Exception as exc:
            if self._failure is None:
                self._failure = (
                    exc
                    if isinstance(exc, WebSocketError | WebRTCError)
                    else WebSocketError(f"The Janus signalling reader stopped: {exc}")
                )
        finally:
            self._event_wakeup.set()
            self._up.set()
            self._fail_acks(
                str(self._failure)
                if self._failure is not None
                else "The signalling socket closed before Janus answered"
            )

    async def _read(self) -> dict[str, Any]:
        """Read one message off the signalling socket.

        Returns:
            The decoded message.

        Raises:
            _Finished: The socket closed, cleanly or otherwise.
            ResponseError: Janus sent something that is not a JSON object.
        """
        if self._connection is None:
            raise _Finished
        try:
            raw = await self._connection.recv()
        except websockets.exceptions.ConnectionClosedOK as exc:
            raise _Finished from exc
        except websockets.exceptions.ConnectionClosed as exc:
            raise WebSocketError(
                f"The Janus signalling connection broke: {exc}"
            ) from exc
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise ResponseError(f"Janus sent a {type(message).__name__}, not an object")
        return message

    def _route(self, message: dict[str, Any]) -> None:
        """Hand one message to whoever is waiting for it.

        A message can be two things at once: Janus answers a plugin request
        synchronously *and* the plugin pushes its own event afterwards, and
        both can carry a transaction. So this does not choose — it resolves
        the acknowledgement if one is waiting, and separately routes anything
        the plugin itself said.

        Args:
            message: The decoded message.
        """
        kind = message.get("janus")
        transaction = message.get("transaction")
        if isinstance(transaction, str):
            future = self._acks.get(transaction)
            if future is not None and not future.done():
                future.set_result(message)
        if kind == "webrtcup":
            self._up.set()

        push = self._plugin_push(message)
        if push is not None:
            jsep = message.get("jsep")
            self._pushes.put_nowait(
                (push, jsep if isinstance(jsep, dict) else None),
            )
        elif kind in ("success", "ack", "error"):
            # An answer to something this client sent, already handed over.
            return

        self._remember(message)

    def _plugin_push(self, message: dict[str, Any]) -> WebRTCPluginEvent | None:
        """Pick the ustreamer plugin's own words out of a message.

        Janus wraps a plugin's synchronous return value the same way it wraps
        a push, so the two are told apart by what is inside: the plugin
        stamps everything it *pushes* with ``ustreamer: "event"``, and its
        synchronous return is a bare ``{"ok": true}``.

        Args:
            message: The decoded message.

        Returns:
            What the plugin said, or ``None`` when it said nothing.
        """
        plugindata = message.get("plugindata")
        if not isinstance(plugindata, dict):
            return None
        data = plugindata.get("data")
        if not isinstance(data, dict) or data.get("ustreamer") != "event":
            return None
        try:
            return WebRTCPluginEvent.model_validate(data)
        except ValidationError as exc:
            logger.warning("Skipping a plugin event aiopikvm cannot read: %s", exc)
            return None

    def _remember(self, message: dict[str, Any]) -> None:
        """Buffer a message for [`events()`][aiopikvm.WebRTCSession.events].

        Args:
            message: The decoded message.
        """
        try:
            event = WebRTCEvent.model_validate(message)
        except ValidationError as exc:
            logger.warning("Skipping a Janus message aiopikvm cannot read: %s", exc)
            return
        if len(self._events) >= _EVENT_BUFFER:
            self._events.popleft()
            logger.debug("Dropping the oldest buffered Janus event")
        self._events.append(event)
        self._event_wakeup.set()

    # --- Odds and ends ---------------------------------------------------

    def _ensure_open(self) -> None:
        """Check that there is a session to act on.

        Raises:
            WebRTCError: There is not.
        """
        if self._connection is None or self._session_id is None:
            raise WebRTCError(
                "This WebRTC session is not open; use it as an async context "
                "manager: 'async with kvm.webrtc() as rtc:'"
            )

    def _fail_acks(self, message: str) -> None:
        """Wake everything waiting on an answer that will never come.

        Args:
            message: What to tell each of them.
        """
        for future in list(self._acks.values()):
            if not future.done():
                future.set_exception(WebSocketError(message))
        self._acks.clear()

    @contextlib.contextmanager
    def _told_the_caller(self) -> Iterator[None]:
        """Mark a signalling failure leaving this block as one the caller saw.

        [`__aexit__`][aiopikvm.WebRTCSession.__aexit__] raises the reader's
        recorded failure when the block ended cleanly and nothing else has
        mentioned it. A call that has just failed in the caller's own hands is
        exactly that something else, and the same breakage arriving a second
        time out of the ``async with`` they were leaving is not news.

        Only the public calls are wrapped. `_farewell()` and the keepalive
        task send through the same code and swallow what comes back — marking
        a failure where it is *raised* would mark it for them too, and the
        teardown would then have nothing to report at all.

        What is marked is the *recorded* failure, so a request Janus refuses
        on a link that is perfectly healthy marks nothing: the session goes
        on, and a break that comes later still has to be reported. A socket
        that broke under the call is recorded here when the reader has not
        caught up yet, which is what stops the reader recording its own copy
        a moment later and the teardown raising that over a block the caller
        chose to leave.

        Yields:
            Nothing. The block runs, and a signalling failure out of it is
            noted as reported on its way to the caller.
        """
        try:
            yield
        except (WebRTCError, WebSocketError) as exc:
            if isinstance(exc, WebSocketError) and self._failure is None:
                self._failure = exc
            if self._failure is not None:
                self._reported = True
            raise

    def _raise_failure(self) -> None:
        """Report the reader's failure, once.

        Raises:
            WebRTCError: The reader stopped on a Janus-level failure.
            WebSocketError: The reader stopped because the socket broke.
        """
        if self._failure is not None:
            self._reported = True
            raise self._failure

    @staticmethod
    async def _stop(task: asyncio.Task[None] | None) -> None:
        """Cancel a background task and wait for it to notice.

        Args:
            task: The task, or ``None`` when there is none.
        """
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _peer_connection(ice_servers: Sequence[str]) -> Callable[[], RTCPeerConnection]:
    """Build the factory that makes the aiortc peer connection.

    The import is done here, before anything reaches the device, so that a
    missing extra is reported without a session having been created on it.

    Args:
        ice_servers: STUN or TURN URLs to gather through, possibly none.

    Returns:
        A callable that builds the configured peer connection.

    Raises:
        ConfigurationError: aiortc is not installed.
    """
    try:
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection
    except ImportError as exc:
        raise ConfigurationError(
            "WebRTC needs aiortc, which aiopikvm does not install by default: "
            "pip install 'aiopikvm[webrtc]'. The other two video paths need "
            "nothing extra — see PiKVM.media_ws() and the streamer resource."
        ) from exc

    # An empty list, never None: aiortc reads None as "use my defaults" and
    # its default is a public STUN server. Passing the list through as it
    # arrived is what makes "no ice_servers" mean no third party at all.
    configuration = RTCConfiguration(
        iceServers=[RTCIceServer(urls=url) for url in ice_servers]
    )
    return lambda: RTCPeerConnection(configuration)


def _identifier(answer: dict[str, Any], what: str) -> int:
    """Read the id out of a ``create`` or ``attach`` answer.

    Args:
        answer: What Janus sent back.
        what: What the id names, for the error message.

    Returns:
        The id.

    Raises:
        ResponseError: The answer carried no usable id.
    """
    data = answer.get("data")
    identifier = data.get("id") if isinstance(data, dict) else None
    if not isinstance(identifier, int):
        raise ResponseError(
            f"Janus answered without a {what} id, which usually means a Janus "
            "version aiopikvm does not know about yet"
        )
    return identifier


def _features(push: WebRTCPluginEvent) -> WebRTCFeatures:
    """Read the feature block out of the plugin's answer.

    Args:
        push: What the plugin pushed.

    Returns:
        What it said it can do.

    Raises:
        ResponseError: The answer was not a feature announcement.
    """
    if push.result is None or push.result.features is None:
        raise ResponseError(
            "The ustreamer plugin answered 'features' with something else, "
            "which usually means a ustreamer version aiopikvm does not know "
            "about yet"
        )
    return push.result.features


def _offer(push: WebRTCPluginEvent, jsep: dict[str, Any] | None) -> str:
    """Read the SDP offer out of the plugin's answer to ``watch``.

    Args:
        push: What the plugin pushed.
        jsep: The SDP block beside it, when there was one.

    Returns:
        The offer.

    Raises:
        ResponseError: There was no offer to take.
    """
    status = push.result.status if push.result is not None else None
    sdp = jsep.get("sdp") if jsep is not None else None
    if jsep is None or jsep.get("type") != "offer" or not isinstance(sdp, str):
        raise ResponseError(
            f"The ustreamer plugin answered 'watch' with {status!r} and no "
            "offer to answer, which usually means a ustreamer version "
            "aiopikvm does not know about yet"
        )
    return sdp
