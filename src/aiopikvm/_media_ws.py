"""WebSocket client for the kvmd-media daemon — live video frames.

kvmd-media runs beside kvmd and serves ``GET /api/media/ws`` through the same
nginx and the same auth chain as everything else, so a refused upgrade arrives
as a plain HTTP response and is reported with the same exceptions.

The socket has two modes, and which one it is is decided in the query string.
With ``?video=<format>`` it is *pure*: the daemon starts sending during the
handshake and every binary message afterwards is one frame of raw video, with
no framing of its own. Without it the socket is the one kvmd's own web UI
opens: it announces what it can send, waits to be asked with a ``start``
event, and then wraps each frame in an operation byte and a keyframe flag.

Pure is the simpler thing to consume and is what this client opens by default.
The regular mode is worth the extra step when the keyframe flag matters — a
decoder joining a stream has to start on one — or when the caller wants the
daemon's own metadata rather than a second REST call for it.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import websockets
import websockets.asyncio.client
from pydantic import ValidationError

from aiopikvm._constants import DEFAULT_TIMEOUT, DEFAULT_VERIFY_SSL, AuthMode
from aiopikvm._exceptions import (
    ConfigurationError,
    ResponseError,
    WebSocketError,
)
from aiopikvm._tls import CertTypes, VerifyTypes
from aiopikvm._ws import (
    _WS_PING_INTERVAL,
    _WS_PING_TIMEOUT,
    _credential_headers,
    _open,
    _ws_url,
)
from aiopikvm.models.media import MediaFrame, MediaState

logger = logging.getLogger(__name__)

_OP_PING = 0
_OP_FRAME = 1
_OP_PONG = 255
"""The daemon's binary operations, as dispatched by ``exposed_ws(<int>)``.

Operation 1 means different things in each direction, which is the daemon's
own numbering: from the client it asks for a keyframe, from the daemon it
carries one frame of video.
"""

_MEDIA_MAX_QUEUE = 64
"""How many frames to buffer before *websockets* stops reading the socket.

Its own default is 16, which at twenty frames a second is under a second of
video. Once the queue is full *websockets* pauses the transport, and since it
parses frames — its own keepalive pongs included — only while reading, the
keepalive then times out and kills a connection whose only problem was a
consumer having a slow moment. See ``ping_timeout`` on
[`PiKVM.media_ws()`][aiopikvm.PiKVM.media_ws] for the other half of that.
"""


class _Closed(Exception):
    """Internal signal: the daemon closed the connection cleanly."""


class MediaWebSocket:
    """WebSocket client for live video from the kvmd-media daemon.

    Usage:

        async with kvm.media_ws() as media:
            async for frame in media.frames():
                decoder.feed(frame.data)

    The daemon only has something to send while the streamer is running, and
    kvmd runs the streamer while at least one connected session asks for
    video. Opening this socket is not that ask — it is a separate daemon.
    Hold a [`PiKVM.ws()`][aiopikvm.PiKVM.ws] open alongside, or the video
    stops arriving with nothing to say why. That socket reads itself, so
    holding it open is the whole of it.
    """

    def __init__(
        self,
        url: str,
        *,
        user: str,
        passwd: str | Callable[[], str],
        auth: AuthMode = "headers",
        token: str | Callable[[], str] = "",
        verify_ssl: VerifyTypes = DEFAULT_VERIFY_SSL,
        cert: CertTypes | None = None,
        proxy: str | None = None,
        trust_env: bool = True,
        video: str | None = "h264",
        follow_redirects: bool = False,
        open_timeout: float = DEFAULT_TIMEOUT,
        close_timeout: float = DEFAULT_TIMEOUT,
        max_size: int | None = None,
        max_queue: int | None = None,
        ping_interval: float | None = _WS_PING_INTERVAL,
        ping_timeout: float | None = _WS_PING_TIMEOUT,
    ) -> None:
        """Prepare a connection.

        Args:
            url: PiKVM base URL, ``https://`` or ``http://``.
            user: kvmd user name.
            passwd: Password, TOTP code appended if the device asks for one.
                A zero-argument callable is called when the handshake is made,
                so a rotating code is the one current then.
            auth: Which credential the handshake carries; ``"cookie"`` needs
                *token* and ignores *user* and *passwd*.
            token: Session token for ``auth="cookie"``. A callable is
                called when the handshake is made, for the same reason
                *passwd* takes one: a session opened or refreshed after
                this object was built is the one that goes out.
            verify_ssl: What to trust; see
                [`VerifyTypes`][aiopikvm.VerifyTypes]. Off by default, the
                same as [`PiKVM`][aiopikvm.PiKVM]: a stock device serves a
                self-signed certificate.
            cert: Client certificate to present.
            proxy: Proxy URL to reach the device through. ``None`` leaves it
                to the environment, unless *trust_env* says otherwise.
            trust_env: Read the proxy configuration from the environment.
            video: The format to stream, which opens the socket in pure mode:
                the daemon starts sending during the handshake and every
                binary message is one raw frame. ``None`` opens the regular
                socket instead, where nothing streams until
                [`start()`][aiopikvm.MediaWebSocket.start] asks for a format
                and each frame carries a keyframe flag. A format the daemon
                does not serve is refused during the handshake — the formats
                it has are on
                [`MediaResource.get_state()`][aiopikvm.resources.media.MediaResource.get_state].
            follow_redirects: Follow a redirected handshake instead of raising
                [`RedirectError`][aiopikvm.RedirectError]. Off by default: the
                upgrade carries the credential in a header — the password,
                or the session token under ``auth="cookie"``.
            open_timeout: Seconds to wait for the handshake, and for the
                daemon's opening announcement on a regular socket.
            close_timeout: Seconds to wait for the closing handshake.
            max_size: Largest message to accept, in bytes. ``None``, the
                default, accepts any: a message here is one video frame, and
                a keyframe of a large screen is easily past the megabyte
                *websockets* allows by default — which would not truncate the
                frame but close the connection.
            max_queue: How many frames *websockets* buffers before it stops
                reading the socket, ``None`` for this client's own default.
                See [`PiKVM.media_ws()`][aiopikvm.PiKVM.media_ws].
            ping_interval: Seconds between *websockets*' own keepalive pings,
                ``None`` to send none.
            ping_timeout: Seconds to wait for a keepalive pong before
                declaring the link dead, ``None`` to wait forever.

        Raises:
            ConfigurationError: If the URL scheme is not ``https`` or ``http``.
        """
        path = "/api/media/ws"
        if video is not None:
            path = f"{path}?video={quote(video, safe='')}"
        self._url = f"{_ws_url(url)}{path}"
        self._user = user
        self._passwd = passwd
        self._auth = auth
        self._token = token
        self._verify_ssl = verify_ssl
        self._cert = cert
        self._proxy = proxy
        self._trust_env = trust_env
        self._video = video
        self._follow_redirects = follow_redirects
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._max_size = max_size
        self._max_queue = _MEDIA_MAX_QUEUE if max_queue is None else max_queue
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._connection: websockets.asyncio.client.ClientConnection | None = None
        self._media: MediaState | None = None

    @property
    def pure(self) -> bool:
        """Whether this socket was opened with a format.

        A pure socket sends raw frames and nothing else. The regular one
        announces itself, answers pings, and flags its keyframes.
        """
        return self._video is not None

    @property
    def media(self) -> MediaState | None:
        """What the daemon said it can send, once it has said it.

        A regular socket announces this during
        [`__aenter__()`][aiopikvm.MediaWebSocket.__aenter__], so it is set by
        the time the block runs. A pure socket never says it — it is already
        streaming the format it was asked for — so this stays ``None`` there;
        [`MediaResource.get_state()`][aiopikvm.resources.media.MediaResource.get_state]
        is where to read it in that case.
        """
        return self._media

    async def __aenter__(self) -> Self:
        """Open the connection.

        On a regular socket this also reads the daemon's opening
        announcement, so that [`media`][aiopikvm.MediaWebSocket.media] is
        filled in before the block starts.

        Returns:
            This client, connected.

        Raises:
            ConfigurationError: Under ``auth="cookie"``, nothing has logged
                in, so there is no session token to send. The credential is
                read here rather than when the socket was built, so a session
                opened in between is the one that goes out — and one that
                never was is reported here. For a socket built by a
                [`PiKVM`][aiopikvm.PiKVM] client, so is that client having
                been closed, or never entered, since its cookie jar is where
                the token is read from. A login that came back without a
                token, kvmd running with authentication off, is not a session
                that never was: the handshake then carries no credential,
                which is what such a device accepts.
            AuthError: kvmd refused the credentials during the upgrade — 401
                when none reached it, 403 when the ones that did were
                rejected.
            RedirectError: The upgrade was redirected and *follow_redirects*
                is off. Following it would resend the credential to the target.
            APIError: The upgrade was rejected for another reason. Asking for
                a format the daemon does not serve is this one — HTTP 400
                with ``ValidatorError``, refused before the socket exists.
            ResponseError: The daemon's announcement was not the object this
                release knows.
            WebSocketError: The connection could not be established: DNS, TLS,
                timeout, or a server that does not speak WebSocket.
        """
        self._connection = await _open(
            self._url,
            headers=self._credential_headers(),
            verify_ssl=self._verify_ssl,
            cert=self._cert,
            proxy=self._proxy,
            trust_env=self._trust_env,
            follow_redirects=self._follow_redirects,
            open_timeout=self._open_timeout,
            close_timeout=self._close_timeout,
            max_size=self._max_size,
            max_queue=self._max_queue,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )

        self._media = None
        if not self.pure:
            try:
                await self._read_media()
            except BaseException:
                # __aexit__ never runs for a failed __aenter__, and the socket
                # is open by now: kvmd would keep it until the process ends.
                await self.__aexit__(None, None, None)
                raise
        return self

    def _credential_headers(self) -> dict[str, str]:
        """Build the credential headers the upgrade request carries.

        Returns:
            The headers for this socket's auth mode.

        Raises:
            ConfigurationError: Under ``auth="cookie"``, nothing has logged
                in, so there is no session token to send. Only a socket built
                by [`PiKVM.media_ws()`][aiopikvm.PiKVM.media_ws] can say
                that: one built directly was handed whatever token it holds,
                and sends no credential header at all when that is empty.
        """
        return _credential_headers(self._auth, self._user, self._passwd, self._token)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the connection, whatever happened inside the block.

        Args:
            exc_type: Type of the exception the block raised, if any.
            exc_val: The exception the block raised, if any.
            exc_tb: Traceback of that exception, if any.
        """
        if self._connection is not None:
            try:
                await self._connection.close()
            finally:
                self._connection = None

    async def frames(self) -> AsyncIterator[MediaFrame]:
        """Iterate over the video frames as they arrive.

        Nothing arrives on a regular socket until
        [`start()`][aiopikvm.MediaWebSocket.start] has asked for a format, and
        nothing arrives on either until the daemon has a keyframe to open
        with: it holds everything back until then, since a decoder handed a
        delta frame first has nothing to apply it to. That is normally the
        next one the encoder produces on its own; on a stream configured with
        a long group of pictures it can be a while, and
        [`request_keyframe()`][aiopikvm.MediaWebSocket.request_keyframe] is
        the way to stop waiting.

        The iteration ends when either side closes the connection cleanly. A
        connection that breaks instead — the device rebooting, the streamer
        being restarted under it, the network going away — raises, because a
        caller that only sees the loop finish cannot tell "there is no more
        video" from "the video stopped arriving".

        Anything that is not a frame is consumed here: the daemon's
        announcement, its answer to a ping, and any operation this release
        does not know.

        Yields:
            Each frame the daemon sent.

        Raises:
            ResponseError: The daemon sent an announcement this release cannot
                read.
            WebSocketError: The client is not connected, or the connection
                broke instead of closing cleanly.
        """
        while True:
            try:
                message = await self._recv()
            except _Closed:
                return
            frame = self._route(message)
            if frame is not None:
                yield frame

    async def start(
        self, *, media_type: str = "video", media_format: str = "h264"
    ) -> None:
        """Ask a regular socket to start streaming.

        The daemon ignores a format it does not have, in silence and without
        closing the socket, so a
        [`frames()`][aiopikvm.MediaWebSocket.frames] loop that never yields is
        what a typo here looks like. The formats the daemon does have are on
        [`MediaResource.get_state()`][aiopikvm.resources.media.MediaResource.get_state],
        and on [`media`][aiopikvm.MediaWebSocket.media] once this socket is
        open.

        Args:
            media_type: Which pipeline to start. ``"video"`` is the only one
                kvmd-media has.
            media_format: The format to send, e.g. ``"h264"``.

        Raises:
            ConfigurationError: This socket was opened with a format, so it
                has been streaming since the handshake and has no handler for
                this event.
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        if self.pure:
            raise ConfigurationError(
                f"This media socket was opened with video={self._video!r}, "
                "which starts the stream during the handshake; start() is "
                "for a socket opened with video=None"
            )
        await self._send(
            json.dumps(
                {
                    "event_type": "start",
                    "event": {"type": media_type, "format": media_format},
                }
            ),
            "start",
        )

    async def request_keyframe(self) -> None:
        """Ask the daemon for a keyframe now.

        Works on both kinds of socket. Use it when the stream has to be
        joined quickly — the daemon sends nothing until it has a keyframe, and
        on a stream whose group of pictures is long that can be several
        seconds away — or after a decoder has lost its reference and needs a
        fresh one.

        There is no acknowledgement: the keyframe simply turns up as the next
        frame with
        [`MediaFrame.key`][aiopikvm.MediaFrame] set, or, on a pure socket,
        as the next one whose NAL units start with a parameter set.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send(bytes([_OP_FRAME]), "keyframe request")

    async def ping(self) -> None:
        """Send the daemon's application-level ping.

        This is not a round trip: the answer arrives on the socket like
        everything else and is consumed by
        [`frames()`][aiopikvm.MediaWebSocket.frames], which has no way to hand
        it back. Keeping the connection alive needs neither — *websockets*
        sends a protocol ping on its own — so this is only useful for making
        the daemon's own event loop prove it is running.

        Raises:
            ConfigurationError: This socket was opened with a format. The
                daemon checks for that before answering a ping, so a pure
                socket never would.
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        if self.pure:
            raise ConfigurationError(
                f"This media socket was opened with video={self._video!r}, "
                "and the daemon does not answer pings on one"
            )
        await self._send(bytes([_OP_PING]), "ping")

    async def _read_media(self) -> None:
        """Read the announcement a regular socket opens with.

        Raises:
            ResponseError: The first message was not the announcement, or was
                one this release cannot read.
            WebSocketError: The connection broke or closed before it arrived.
        """
        try:
            async with asyncio.timeout(self._open_timeout):
                message = await self._recv()
        except _Closed as exc:
            raise WebSocketError(
                "The media socket closed before the daemon said what it can send"
            ) from exc
        except TimeoutError as exc:
            raise WebSocketError(
                f"The media daemon did not say what it can send within "
                f"{self._open_timeout} s"
            ) from exc
        if isinstance(message, str):
            self._route_text(message)
        if self._media is None:
            raise ResponseError(
                "The media socket opened with something other than the "
                "daemon's announcement; this usually means a kvmd version "
                "aiopikvm does not know about yet"
            )

    async def _recv(self) -> str | bytes:
        """Read one message off the socket.

        Returns:
            The message, text or binary.

        Raises:
            _Closed: The daemon closed the connection cleanly.
            WebSocketError: The client is not connected, or the connection
                broke instead of closing cleanly.
        """
        if self._connection is None:
            raise WebSocketError("Not connected")
        try:
            return await self._connection.recv()
        except websockets.exceptions.ConnectionClosedOK as exc:
            raise _Closed from exc
        except websockets.exceptions.ConnectionClosed as exc:
            raise WebSocketError(f"Connection lost while reading video: {exc}") from exc
        except websockets.exceptions.WebSocketException as exc:
            raise WebSocketError(f"Failed to read from the socket: {exc}") from exc

    async def _send(self, frame: str | bytes, what: str) -> None:
        """Send one frame, whichever encoding it is in.

        Args:
            frame: The frame to send; text if it is a string, binary if not.
            what: What it is, for the error message.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        if self._connection is None:
            raise WebSocketError("Not connected")
        try:
            await self._connection.send(frame)
        except websockets.exceptions.WebSocketException as exc:
            raise WebSocketError(f"Failed to send {what}: {exc}") from exc

    def _route(self, message: str | bytes) -> MediaFrame | None:
        """Turn one message into a frame, or consume it.

        Args:
            message: The message as it arrived.

        Returns:
            The frame it carried, or ``None`` when it carried none.

        Raises:
            ResponseError: The message was an announcement this release cannot
                read.
        """
        if isinstance(message, str):
            self._route_text(message)
            return None
        if self.pure:
            # Nothing wraps the frame: the whole message is the video.
            return MediaFrame(data=message)
        if not message:
            logger.warning("Skipping an empty binary media frame")
            return None
        op = message[0]
        if op == _OP_FRAME:
            if len(message) < 2:
                logger.warning("Skipping a video frame with no keyframe flag")
                return None
            return MediaFrame(data=message[2:], key=bool(message[1]))
        if op != _OP_PONG:
            logger.warning("Skipping a binary media frame with unknown op %d", op)
        return None

    def _route_text(self, message: str) -> None:
        """Parse a JSON frame and note what it says.

        Args:
            message: The text frame as it arrived.

        Raises:
            ResponseError: The message was an announcement this release cannot
                read.
        """
        try:
            event = json.loads(message)
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed media message: %s", exc)
            return
        if not isinstance(event, dict) or event.get("event_type") != "media":
            logger.warning(
                "Skipping an unexpected media message: %.80s", message.strip()
            )
            return
        payload: Any = event.get("event")
        try:
            self._media = MediaState.model_validate(payload)
        except ValidationError as exc:
            raise ResponseError(
                "The media daemon announced a payload MediaState cannot "
                "parse. This usually means a kvmd version aiopikvm does not "
                f"know about yet:\n{exc}"
            ) from exc
