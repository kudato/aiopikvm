"""WebSocket client for PiKVM — realtime events and HID input.

kvmd exposes one socket, ``GET /api/ws``. It carries the event stream every
subsystem broadcasts its state on, and it takes HID input in the other
direction. The upgrade goes through the same auth chain as the REST API, so a
refused handshake arrives as a plain HTTP response and is reported with the
same exceptions.

Two encodings share that socket. Text frames are the JSON events, ``{"event_type":
..., "event": ...}`` in both directions. Binary frames are kvmd's compact input
channel: the first byte is an operation number, and the rest is that operation's
payload. kvmd's own web UI sends every keystroke and mouse move that way, and
only ever answers on it with one operation of its own — the pong. This client
speaks JSON by default and switches the input direction to binary when built
with ``binary=True``; incoming frames of either kind are understood regardless.
"""

import asyncio
import base64
import contextlib
import dataclasses
import json
import logging
import ssl
import struct
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from types import TracebackType
from typing import Any, Literal, NamedTuple, Self
from urllib.parse import urlparse, urlunparse

import websockets
import websockets.asyncio.client
import websockets.http11
from pydantic import BaseModel, ValidationError

from aiopikvm._constants import AuthMode
from aiopikvm._exceptions import (
    APIError,
    ConfigurationError,
    ResponseError,
    WebSocketError,
    _error_fields_from_bytes,
    _status_error,
)
from aiopikvm._tls import CertTypes, VerifyTypes, build_ssl_context
from aiopikvm.models.atx import ATXState
from aiopikvm.models.gpio import GPIOState
from aiopikvm.models.hid import HIDKeymaps, HIDState
from aiopikvm.models.info import InfoState
from aiopikvm.models.msd import MSDState
from aiopikvm.models.streamer import OCRInfo, StreamerState
from aiopikvm.models.switch import SwitchState
from aiopikvm.resources.hid import MouseButton

logger = logging.getLogger(__name__)

_OP_PING = 0
_OP_KEY = 1
_OP_MOUSE_BUTTON = 2
_OP_MOUSE_MOVE = 3
_OP_MOUSE_RELATIVE = 4
_OP_MOUSE_WHEEL = 5
_OP_PONG = 255
"""kvmd's binary operations, as dispatched by ``exposed_ws(<int>)``."""

_NAME_LIMIT = 32
"""kvmd reads a key or button name out of ``data[1:33]``; longer is truncated."""

_MOVE_MIN = -32768
_MOVE_MAX = 32767
"""kvmd's absolute pointer range, clamped by ``valid_hid_mouse_move``."""

_DELTA_MIN = -127
_DELTA_MAX = 127
"""kvmd's wheel and relative step range, clamped by ``valid_hid_mouse_delta``."""

_PENDING_LIMIT = 1024
"""How many events the reader may buffer before it starts dropping them."""

_WS_MAX_SIZE = 2**20
_WS_MAX_QUEUE = 16
_WS_PING_INTERVAL = 20.0
_WS_PING_TIMEOUT = 20.0
"""*websockets*' own defaults, spelled out so that overriding one is a choice.

They are named here rather than left implicit because the media socket
overrides them: a video frame is bigger than a control event, and a consumer
that falls behind on video is a normal thing that must not be mistaken for a
dead link.
"""


class KvmdVersion(NamedTuple):
    """The kvmd protocol version from the ``loop`` event.

    kvmd sends it as the first thing on every connection, and it is the only
    version signal the socket carries. Being a tuple, it compares the way a
    version should: ``ws.version >= (4, 100)``.

    Attributes:
        major: Major version, ``4`` for the kvmd 4.x series.
        minor: Minor version, e.g. ``206`` for kvmd 4.206.
    """

    major: int
    minor: int


@dataclasses.dataclass(frozen=True, slots=True)
class DeviceState:
    """Everything the socket has said about the device so far.

    One of these comes out of
    [`PiKVMWebSocket.states()`][aiopikvm.PiKVMWebSocket.states] per event that
    changed something, with the subsystem that event was about validated
    against the same model its REST endpoint returns. A field is ``None``
    until kvmd has sent that subsystem — which it does for all of them when
    the socket opens, except on a device that has the subsystem switched off.

    Attributes:
        updated: Event type behind this snapshot, e.g. ``"atx"``. Empty on a
            snapshot nothing has been merged into yet.
        atx: Power and LED state, as ``GET /api/atx`` returns it.
        gpio: GPIO scheme, view and pin state.
        hid: Keyboard, mouse and jiggler state.
        hid_keymaps: Keyboard layouts installed on the device.
        msd: Mass storage drive and storage state.
        ocr: Whether OCR is enabled, and the languages it has.
        streamer: Streamer state, features, limits and parameters.
        switch: PiKVM Switch model, port state and summary.
        clients: How many connected sessions asked kvmd for video. kvmd
            broadcasts it to everybody whenever a session comes or goes, this
            one included.
        info: The ``/api/info`` subsystems, merged as they arrive. kvmd sends
            one submanager at a time — ``uptime``, ``health``, ``system`` —
            so every attribute of it is optional and fills in as the events
            come. It is the per-submanager shape, never the legacy one.
    """

    updated: str = ""
    atx: ATXState | None = None
    gpio: GPIOState | None = None
    hid: HIDState | None = None
    hid_keymaps: HIDKeymaps | None = None
    msd: MSDState | None = None
    ocr: OCRInfo | None = None
    streamer: StreamerState | None = None
    switch: SwitchState | None = None
    clients: int | None = None
    info: InfoState | None = None


_STATE_MODELS: dict[str, tuple[type[BaseModel], str]] = {
    "atx": (ATXState, ""),
    "gpio": (GPIOState, ""),
    "hid": (HIDState, ""),
    "hid_keymaps": (HIDKeymaps, "keymaps"),
    "info": (InfoState, ""),
    "msd": (MSDState, ""),
    "ocr": (OCRInfo, ""),
    "streamer": (StreamerState, ""),
    "switch": (SwitchState, ""),
}
"""Model for each subsystem event, and the key to unwrap before validating.

Only ``hid_keymaps`` has one: kvmd sends that state inside a ``keymaps``
object, the way ``GET /api/hid/keymaps`` returns it. The ``ocr`` event is the
other way round — the REST endpoint wraps it in ``ocr`` and the event does not.
"""


class _Finished(Exception):
    """Internal signal: the server closed the connection cleanly."""


class _Connector(websockets.asyncio.client.connect):
    """``connect()`` that reports redirects instead of following them.

    *websockets* follows up to ten redirects on its own, resending the
    credential headers to wherever each one points. The REST client refuses
    to do that unless asked, and the WebSocket carries the same password, so
    it defaults to the same refusal.
    """

    def __init__(
        self,
        uri: str,
        *,
        follow_redirects: bool = False,
        additional_headers: dict[str, str],
        ssl_context: ssl.SSLContext | None,
        proxy: str | Literal[True] | None,
        open_timeout: float,
        close_timeout: float,
        max_size: int | None = _WS_MAX_SIZE,
        max_queue: int = _WS_MAX_QUEUE,
        ping_interval: float | None = _WS_PING_INTERVAL,
        ping_timeout: float | None = _WS_PING_TIMEOUT,
    ) -> None:
        """Prepare the handshake.

        The arguments are spelled out rather than forwarded as ``**kwargs``
        so that they stay type-checked; ``connect`` takes many more, and none
        of them are used here.

        Args:
            uri: ``ws://`` or ``wss://`` URI to connect to.
            follow_redirects: Follow a redirect instead of reporting it.
            additional_headers: Headers to add to the upgrade request.
            ssl_context: TLS configuration, or ``None`` for a plain socket.
            proxy: A proxy URL, ``True`` to take one from the environment —
                which is *websockets*' own default — or ``None`` to connect
                directly.
            open_timeout: Seconds to wait for the handshake.
            close_timeout: Seconds to wait for the closing handshake.
            max_size: Largest message to accept, or ``None`` for no limit.
            max_queue: How many messages to buffer before pausing the read.
            ping_interval: Seconds between keepalive pings, ``None`` for none.
            ping_timeout: Seconds to wait for a keepalive pong, ``None`` to
                wait forever.
        """
        self._follow_redirects = follow_redirects
        super().__init__(
            uri,
            additional_headers=additional_headers,
            ssl=ssl_context,
            proxy=proxy,
            open_timeout=open_timeout,
            close_timeout=close_timeout,
            max_size=max_size,
            max_queue=max_queue,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )

    def process_redirect(self, exc: Exception) -> Exception | str:
        """Decide what to do with a handshake response.

        Args:
            exc: The exception the handshake produced.

        Returns:
            The URI to follow when redirects are allowed and this is one,
            otherwise the exception, which makes *websockets* raise it — and
            which
            [`PiKVMWebSocket.__aenter__()`][aiopikvm.PiKVMWebSocket.__aenter__]
            turns into a [`PiKVMError`][aiopikvm.PiKVMError].
        """
        if not self._follow_redirects:
            return exc
        try:
            return super().process_redirect(exc)
        except LookupError:
            # websockets reads the Location header with Headers.__getitem__,
            # which raises MultipleValuesError — a LookupError, so nothing
            # up the stack absorbs it — when the header arrives twice. A
            # redirect nobody can resolve is one to report.
            return exc


class PiKVMWebSocket:
    """WebSocket client for PiKVM realtime events and HID input.

    Usage:

        async with kvm.ws() as ws:
            async for event in ws.events():
                print(event)
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
        stream: bool = True,
        binary: bool = False,
        follow_redirects: bool = False,
        open_timeout: float = 10.0,
        close_timeout: float = 10.0,
        max_size: int | None = _WS_MAX_SIZE,
        max_queue: int = _WS_MAX_QUEUE,
        ping_interval: float | None = _WS_PING_INTERVAL,
        ping_timeout: float | None = _WS_PING_TIMEOUT,
    ) -> None:
        """Prepare a connection.

        Args:
            url: PiKVM base URL, ``https://`` or ``http://``.
            user: kvmd user name.
            passwd: Password, TOTP code appended if the device asks for one.
                A zero-argument callable is called when the handshake is
                made, so a rotating code is the one current then rather
                than the one current when this object was built.
            auth: Which credential the handshake carries. The upgrade request
                goes through the same chain a REST call does, so all three
                work; ``"cookie"`` needs *token* and ignores *user* and
                *passwd*.
            token: Session token for ``auth="cookie"``.
            verify_ssl: What to trust; see
                [`VerifyTypes`][aiopikvm.VerifyTypes].
            cert: Client certificate to present.
            proxy: Proxy URL to reach the device through. ``None``
                leaves it to the environment, unless *trust_env* says
                otherwise.
            trust_env: Read the proxy configuration from the
                environment. ``False`` connects directly.
            stream: Ask kvmd to treat this client as a video viewer. kvmd
                counts the sessions that did and runs the streamer while that
                count is above zero, so a client connected with ``False``
                lets the video pipeline stop under it — and
                ``StreamerResource.snapshot()`` then answers HTTP 503 unless
                something else is watching. Off only makes sense for a client
                that reads events and never looks at the picture.
            binary: Send input over kvmd's binary channel instead of as JSON
                events. Both reach the same handlers and the same validators;
                the binary frames are a few bytes each instead of a JSON
                object kvmd has to parse, which is why its own web UI uses
                them for every keystroke and mouse move. Off by default,
                since JSON is what this client has always sent and the
                encoding a packet capture can be read in. The binary channel
                was verified against kvmd 4.206.
            follow_redirects: Follow a redirected handshake instead of raising
                [`RedirectError`][aiopikvm.RedirectError]. Off by default: the
                upgrade carries the password in a header, and following the
                redirect hands it to whatever the redirect points at.
            open_timeout: Seconds to wait for the handshake.
            close_timeout: Seconds to wait for the closing handshake.
            max_size: Largest frame to accept, in bytes, or ``None`` for no
                limit. kvmd's events are small; the cap is *websockets*' own.
            max_queue: How many frames the transport may buffer before it
                pauses reading. This connection reads continuously, so the
                queue is drained as fast as it fills and the setting is here
                for a caller who knows otherwise.
            ping_interval: Seconds between the protocol keepalive pings, or
                ``None`` to send none. This is *websockets*' own keepalive,
                not [`ping()`][aiopikvm.PiKVMWebSocket.ping]; turning it off
                means a link that dies silently is never noticed.
            ping_timeout: Seconds to wait for a keepalive pong before failing
                the connection, or ``None`` to wait forever.

        Raises:
            ConfigurationError: If the URL scheme is not ``https`` or ``http``.
        """
        # kvmd reads the flag with valid_bool, which takes 1/true/yes and
        # 0/false/no and answers 400 to anything else.
        self._url = f"{_ws_url(url)}/api/ws?stream={'1' if stream else '0'}"
        self._user = user
        self._passwd = passwd
        self._auth = auth
        self._token = token
        self._verify_ssl = verify_ssl
        self._cert = cert
        self._proxy = proxy
        self._trust_env = trust_env
        self._binary = binary
        self._follow_redirects = follow_redirects
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._max_size = max_size
        self._max_queue = max_queue
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._connection: websockets.asyncio.client.ClientConnection | None = None
        self._version: KvmdVersion | None = None
        # One task reads the socket and routes what it reads for everybody:
        # an event it buffered is still an event this connection received.
        # Nothing else touches `recv`, so the transport is never left unread.
        self._reader: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()
        self._failure: WebSocketError | None = None
        self._reported = False
        self._pending: deque[dict[str, Any]] = deque()
        self._carry: dict[str, dict[str, Any]] = {}
        self._overflowed = False
        self._pong_waiters: list[asyncio.Future[float]] = []

    async def __aenter__(self) -> Self:
        """Open the connection and start reading it.

        A task begins draining the socket as soon as it is open, whether or
        not anything iterates [`events()`][aiopikvm.PiKVMWebSocket.events].
        That is not an optimisation: *websockets* parses frames in the
        transport callback and pauses reading once its inbound queue fills, so
        a socket nobody reads stops acknowledging the protocol keepalive and
        is dropped about forty seconds in — taking kvmd's streamer with it,
        since kvmd runs it for as long as a session says it wants video.

        Returns:
            This client, connected.

        Raises:
            AuthError: kvmd refused the credentials during the upgrade — 401
                when none reached it, 403 when the ones that did were
                rejected.
            RedirectError: The upgrade was redirected and *follow_redirects*
                is off. Following it would resend the password to the target.
            APIError: kvmd rejected the upgrade for another reason, such as a
                query parameter its validators do not accept, or a proxy in
                front of it answered instead.
            WebSocketError: The connection could not be established: DNS,
                TLS, timeout, or a server that does not speak WebSocket.
        """
        # `ws://` carries no TLS, so there is nothing to configure there;
        # anything the caller asked for would be silently unused.
        ssl_context: ssl.SSLContext | None = None
        if self._url.startswith("wss://"):
            ssl_context = build_ssl_context(self._verify_ssl, self._cert)

        headers = self._credential_headers()

        try:
            self._connection = await _Connector(
                self._url,
                additional_headers=headers,
                ssl_context=ssl_context,
                proxy=(self._proxy or (True if self._trust_env else None)),
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                follow_redirects=self._follow_redirects,
                max_size=self._max_size,
                max_queue=self._max_queue,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        except websockets.exceptions.InvalidStatus as exc:
            # The upgrade never happened: kvmd answered the GET with an
            # ordinary HTTP error, envelope and all.
            raise _handshake_error(exc.response) from exc
        except (
            OSError,
            ValueError,
            websockets.exceptions.WebSocketException,
        ) as exc:
            # ValueError covers the URIs websockets rejects itself, which a
            # redirect can produce even though this one is built from a
            # checked scheme.
            raise WebSocketError(f"Failed to connect: {exc}") from exc

        self._version = None
        self._pending.clear()
        self._carry.clear()
        self._overflowed = False
        self._failure = None
        self._reported = False
        self._wakeup.clear()
        self._start_reader()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the connection, whatever happened inside the block.

        A [`ping()`][aiopikvm.PiKVMWebSocket.ping] still waiting for its
        answer fails here rather than waiting out its timeout: the socket it
        was waiting on is gone.

        A connection that broke while the block was doing something else is
        raised here, and this is the only place it can be: a block that holds
        the socket open without reading it has nowhere else to find out. It
        gives way to whatever the block itself raised, and says nothing when
        the failure has already reached the caller through
        [`events()`][aiopikvm.PiKVMWebSocket.events],
        [`states()`][aiopikvm.PiKVMWebSocket.states] or
        [`ping()`][aiopikvm.PiKVMWebSocket.ping].

        Args:
            exc_type: Type of the exception the block raised, if any.
            exc_val: The exception the block raised, if any.
            exc_tb: Traceback of that exception, if any.

        Raises:
            WebSocketError: The connection broke during the block and nothing
                in it noticed.
        """
        await self._stop_reader()
        if self._connection is not None:
            try:
                await self._connection.close()
            finally:
                self._connection = None
        if exc_type is None and self._failure is not None and not self._reported:
            self._reported = True
            raise self._failure

    def _start_reader(self) -> None:
        """Start the task that reads the socket, unless one is running."""
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._drain())

    async def _stop_reader(self) -> None:
        """Stop that task and fail anything still waiting on what it reads."""
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
        self._fail_pongs("The connection closed before kvmd answered the ping")

    async def _drain(self) -> None:
        """Read the socket for as long as it has anything to say.

        Every frame is routed as it arrives — the version noted, a pong handed
        to whoever asked for one — and every event is kept for
        [`events()`][aiopikvm.PiKVMWebSocket.events]. Reading here rather than
        from `events()` is what keeps the transport drained; see
        [`__aenter__`][aiopikvm.PiKVMWebSocket.__aenter__] for why that
        matters.

        A clean close ends this quietly. Anything else is kept and handed to
        the next caller who asks, or raised by
        [`__aexit__`][aiopikvm.PiKVMWebSocket.__aexit__] if nobody does.
        """
        try:
            while True:
                event = await self._read_one()
                if event is not None:
                    self._buffer(event)
                self._wakeup.set()
        except _Finished:
            pass
        except Exception as exc:
            self._failure = (
                exc
                if isinstance(exc, WebSocketError)
                else WebSocketError(f"The socket reader stopped: {exc}")
            )
        finally:
            self._wakeup.set()
            # Nothing will answer a ping now, either way: a clean close is
            # still a close, and waiting out the timeout says nothing extra.
            self._fail_pongs(
                str(self._failure)
                if self._failure is not None
                else "The connection closed before kvmd answered the ping"
            )

    def _fail_pongs(self, message: str) -> None:
        """Fail every waiting [`ping()`][aiopikvm.PiKVMWebSocket.ping].

        Args:
            message: What to tell each of them. Each gets an exception of its
                own, so one caller's traceback is not another's.
        """
        for waiter in self._pong_waiters:
            if not waiter.done():
                waiter.set_exception(WebSocketError(message))
        self._pong_waiters.clear()

    def _raise_failure(self) -> None:
        """Hand the reader's failure to a caller, once.

        Raises:
            WebSocketError: The connection broke rather than closing cleanly.
        """
        if self._failure is not None:
            self._reported = True
            raise self._failure

    def _credential_headers(self) -> dict[str, str]:
        """Build the credential headers the upgrade request carries.

        Returns:
            The headers for this socket's auth mode.
        """
        return _credential_headers(self._auth, self._user, self._passwd, self._token)

    @property
    def version(self) -> KvmdVersion | None:
        """The kvmd version this connection reported, once it has been read.

        kvmd sends the ``loop`` event carrying it before anything else, and
        the socket is read from the moment it opens, so this fills in shortly
        after the connection is made whether or not anything is iterating
        [`events()`][aiopikvm.PiKVMWebSocket.events]. It is ``None`` until that
        first frame arrives, and on a connection whose ``loop`` event carried
        no usable version.
        """
        return self._version

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Iterate over incoming events.

        Every event is a ``{"event_type": ..., "event": ...}`` object. The
        first one is always ``loop``, carrying the kvmd version; after it
        each subsystem sends its current state once, interleaved with the
        broadcasts other clients trigger, so nothing but ``loop`` arrives in
        a guaranteed order. See the WebSocket guide for the full list.

        Binary frames do not appear here. The only one kvmd sends is the
        answer to [`ping()`][aiopikvm.PiKVMWebSocket.ping], which that method
        consumes; anything else on that channel is logged and dropped, since a
        binary frame is an operation number and a payload, not an event.

        The iteration ends when either side closes the connection cleanly. A
        connection that breaks instead — the device rebooting, the network
        going away, kvmd restarting — raises, because a caller that only sees
        the loop finish cannot tell "kvmd has nothing more to say" from
        "the events stopped arriving".

        Nothing is read here: the socket is drained by a task of its own from
        the moment it opens, and this hands out what that task collected. A
        consumer slower than kvmd is broadcasting therefore falls behind in
        memory rather than on the wire — and once
        1024 events are waiting, the oldest are dropped, merged into the next
        event of their kind so that no field a
        [`states()`][aiopikvm.PiKVMWebSocket.states] snapshot rests on is lost.

        Yields:
            Parsed JSON event dictionaries.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke instead of closing cleanly.
        """
        self._ensure_connected()
        self._start_reader()
        while True:
            while self._pending:
                yield self._next_event()
            reader = self._reader
            if reader is None or reader.done():
                self._raise_failure()
                return
            # Cleared before the recheck, so an event buffered between the two
            # wakes this up rather than being waited past.
            self._wakeup.clear()
            if self._pending or reader.done():
                continue
            await self._wakeup.wait()

    def _next_event(self) -> dict[str, Any]:
        """Take the oldest buffered event, with anything dropped folded in.

        Returns:
            The event, its payload merged over whatever was dropped from the
            same subsystem before it.
        """
        event = self._pending.popleft()
        event_type = event.get("event_type")
        if not isinstance(event_type, str):
            return event
        carried = self._carry.pop(event_type, None)
        if carried is None:
            return event
        payload = event.get("event")
        merged = _merge(carried, payload) if isinstance(payload, dict) else carried
        return {**event, "event": merged}

    async def states(self) -> AsyncIterator[DeviceState]:
        """Iterate over the device state the events add up to.

        kvmd broadcasts a subsystem's state in pieces: the first event for
        each carries all of it, and every event after that carries only what
        changed — a ``streamer`` event with nothing but ``streamer`` in it, an
        ``info`` event with nothing but ``uptime``. Validating one of those on
        its own fails, because most of the model is simply not in it. This
        merges each event into what the same subsystem said before and hands
        back the whole picture, typed, once per event that changed something.

        Only the events that say something about the device produce a
        snapshot; ``loop`` and ``pong`` do not, and neither does an event type
        this release does not know. The kvmd version the ``loop`` event
        carries is on [`version`][aiopikvm.PiKVMWebSocket.version].

        Everything [`events()`][aiopikvm.PiKVMWebSocket.events] does about the
        connection applies here, and the two cannot be iterated over the same
        socket at once: this is [`events()`][aiopikvm.PiKVMWebSocket.events]
        with the states built on top.

        Yields:
            The device as of the event that has just arrived.

        Raises:
            ResponseError: A merged payload did not match its model, which
                means a kvmd this release does not describe correctly. kvmd
                sends every subsystem in full when the socket opens, so a
                partial update always has something to merge into.
            WebSocketError: The client is not connected, or the connection
                broke instead of closing cleanly.
        """
        seen: dict[str, dict[str, Any]] = {}
        state = DeviceState()
        async for event in self.events():
            event_type = event.get("event_type")
            payload = event.get("event")
            if not isinstance(event_type, str) or not isinstance(payload, dict):
                continue
            if event_type == "clients":
                count = payload.get("count")
                if not isinstance(count, int):
                    continue
                state = dataclasses.replace(state, updated=event_type, clients=count)
            elif event_type in _STATE_MODELS:
                merged = _merge(seen.get(event_type, {}), payload)
                seen[event_type] = merged
                state = dataclasses.replace(
                    state,
                    updated=event_type,
                    **{event_type: _as_state(event_type, merged)},
                )
            else:
                continue
            yield state

    async def ping(self, *, timeout: float = 10.0) -> float:
        """Ask kvmd for a pong, and wait for it.

        This is kvmd's application-level ping, not the protocol one: the
        request goes through the same event loop that dispatches HID input and
        broadcasts state, so the answer means that loop is running, not merely
        that something on the other end still holds a TCP socket open. Keeping
        the connection alive needs neither — *websockets* sends a protocol
        ping every 20 seconds by itself and drops the connection when one goes
        unanswered for another 20, which is what turns a silently dead link
        into a [`WebSocketError`][aiopikvm.WebSocketError]. That keepalive
        tells a dead link from a live one only because this connection is
        always being read: a pong is acknowledged where the frames are parsed,
        so a socket left unread fails its own keepalive.

        The answer arrives on the socket like everything else, so the task
        reading it is what hands the pong over, and the events it passes on
        the way are kept for the next
        [`events()`][aiopikvm.PiKVMWebSocket.events] call. The round trip is
        measured from the frame going out to the pong being read, which is not
        affected by how fast anything consumes
        [`events()`][aiopikvm.PiKVMWebSocket.events].

        Args:
            timeout: Seconds to wait for the answer.

        Returns:
            The round trip in seconds.

        Raises:
            WebSocketError: The client is not connected, the connection broke
                or closed before the answer arrived, or kvmd did not answer
                within *timeout*.
        """
        self._ensure_connected()
        self._start_reader()
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[float] = loop.create_future()
        self._pong_waiters.append(waiter)
        try:
            sent_at = loop.time()
            if self._binary:
                await self._send_bin(_OP_PING, b"", "ping")
            else:
                await self._send_event("ping", {})
            async with asyncio.timeout(timeout):
                answered_at = await waiter
            return answered_at - sent_at
        except TimeoutError as exc:
            raise WebSocketError(
                f"kvmd did not answer the ping within {timeout} s"
            ) from exc
        except WebSocketError:
            # The caller has been told the socket is gone, so __aexit__ has
            # nothing left to report.
            self._reported = True
            raise
        finally:
            if waiter in self._pong_waiters:
                self._pong_waiters.remove(waiter)

    def _buffer(self, event: dict[str, Any]) -> None:
        """Keep an event the reader took off the socket.

        The buffer is bounded, because the socket is read whether or not
        anything collects what it says: kvmd broadcasts its state regardless,
        and a caller may hold the connection open only to keep the streamer
        running.

        Dropping the oldest is not quite dropping it. kvmd sends each
        subsystem in full once and then only what changed, so an event lost
        from the front of the queue can leave every later one unusable —
        [`states()`][aiopikvm.PiKVMWebSocket.states] would have nothing to
        merge a partial update into. Its payload is therefore folded into a
        carry and merged back over the next event of the same type, which
        yields exactly what merging all of them in order would have.

        Args:
            event: The event to hand to the next
                [`events()`][aiopikvm.PiKVMWebSocket.events] call.
        """
        if len(self._pending) >= _PENDING_LIMIT:
            if not self._overflowed:
                logger.warning(
                    "Dropping WebSocket events: %d are buffered and nothing "
                    "is reading events()",
                    _PENDING_LIMIT,
                )
                self._overflowed = True
            self._carry_over(self._pending.popleft())
        self._pending.append(event)

    def _carry_over(self, dropped: dict[str, Any]) -> None:
        """Keep what a dropped event said, to merge into the next of its kind.

        Args:
            dropped: The event that did not fit in the buffer.
        """
        event_type = dropped.get("event_type")
        payload = dropped.get("event")
        if isinstance(event_type, str) and isinstance(payload, dict):
            self._carry[event_type] = _merge(self._carry.get(event_type, {}), payload)

    async def _read_one(self) -> dict[str, Any] | None:
        """Read one frame off the socket and route it.

        Returns:
            The event to hand to a caller, or ``None`` when the frame was
            consumed here — a pong, or something unusable.

        Raises:
            _Finished: The server closed the connection cleanly.
            WebSocketError: The client is not connected, or the connection
                broke instead of closing cleanly.
        """
        conn = self._ensure_connected()
        try:
            message = await conn.recv()
        except websockets.exceptions.ConnectionClosedOK as exc:
            raise _Finished from exc
        except websockets.exceptions.ConnectionClosed as exc:
            raise WebSocketError(
                f"Connection lost while reading events: {exc}"
            ) from exc
        except websockets.exceptions.WebSocketException as exc:
            raise WebSocketError(f"Failed to read from the socket: {exc}") from exc
        if isinstance(message, str):
            return self._route_text(message)
        self._route_binary(message)
        return None

    def _route_text(self, message: str) -> dict[str, Any] | None:
        """Parse a JSON frame and note what it says.

        Args:
            message: The text frame as it arrived.

        Returns:
            The parsed event, or ``None`` when it was not one.
        """
        try:
            event = json.loads(message)
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed WebSocket message: %s", exc)
            return None
        if not isinstance(event, dict):
            logger.warning(
                "Skipping a WebSocket message that is not an event object: %s",
                type(event).__name__,
            )
            return None
        event_type = event.get("event_type")
        if event_type == "pong":
            self._resolve_pongs()
        elif event_type == "loop":
            self._note_version(event.get("event"))
        return event

    def _route_binary(self, data: bytes) -> None:
        """Handle a frame from kvmd's binary channel.

        Nothing comes back: no binary frame kvmd sends is an event. The only
        operation it has on this channel is the pong.

        Args:
            data: The binary frame, the operation number first.
        """
        if not data:
            logger.warning("Skipping an empty binary WebSocket frame")
        elif data[0] == _OP_PONG:
            self._resolve_pongs()
        else:
            logger.warning(
                "Skipping a binary WebSocket frame with unknown op %d", data[0]
            )

    def _resolve_pongs(self) -> None:
        """Hand the moment a pong arrived to everything waiting for one."""
        now = asyncio.get_running_loop().time()
        for waiter in self._pong_waiters:
            if not waiter.done():
                waiter.set_result(now)
        self._pong_waiters.clear()

    def _note_version(self, event: Any) -> None:
        """Remember the kvmd version from a ``loop`` event.

        Args:
            event: The event payload, whatever arrived in it.
        """
        version = event.get("version") if isinstance(event, dict) else None
        if not isinstance(version, dict):
            return
        major = version.get("major")
        minor = version.get("minor")
        if isinstance(major, int) and isinstance(minor, int):
            self._version = KvmdVersion(major, minor)

    def _ensure_connected(self) -> websockets.asyncio.client.ClientConnection:
        """Return the active connection or raise.

        Returns:
            The connection.

        Raises:
            WebSocketError: The client is not connected.
        """
        if self._connection is None:
            raise WebSocketError("Not connected")
        return self._connection

    async def _send_frame(self, frame: str | bytes, what: str) -> None:
        """Send one frame, whichever encoding it is in.

        Args:
            frame: The frame to send; text if it is a string, binary if not.
            what: Name of the event for the error message.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        conn = self._ensure_connected()
        try:
            await conn.send(frame)
        except websockets.exceptions.WebSocketException as exc:
            # The caller has just been told the socket is gone, so whatever
            # the reader saw needs no second telling from __aexit__.
            self._reported = True
            raise WebSocketError(f"Failed to send {what!r}: {exc}") from exc

    async def _send_event(self, event_type: str, event: dict[str, Any]) -> None:
        """Send one JSON event frame.

        Args:
            event_type: kvmd event name.
            event: Payload for that event.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_frame(
            json.dumps({"event_type": event_type, "event": event}), event_type
        )

    async def _send_bin(self, op: int, payload: bytes, what: str) -> None:
        """Send one binary frame.

        Args:
            op: kvmd operation number, the first byte of the frame.
            payload: The rest of the frame, that operation's own encoding.
            what: Name of the event for the error message.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_frame(bytes([op]) + payload, what)

    async def send_key(self, key: str, *, state: bool, finish: bool = False) -> None:
        """Send a keyboard key event.

        Args:
            key: Key name, one of kvmd's web names such as ``"KeyA"`` or
                ``"ControlLeft"``; ``aiopikvm.resources.hid.KEY_NAMES`` holds
                every one of them. kvmd ignores an event it cannot map, and
                over this socket it does so without an answer of any kind —
                there is no 400 here to tell a typo from a keystroke that
                landed.
            state: ``True`` for press, ``False`` for release. kvmd holds the
                key until the release arrives.
            finish: Ask kvmd to release the key in the same event that
                pressed it, so a socket that goes away mid-keystroke leaves
                nothing held. It goes out only on a press, the only place
                kvmd acts on it; ``HIDResource.send_key`` and the HID guide
                have the keys it exempts.

        Raises:
            ConfigurationError: The key name cannot go into a binary frame,
                being empty, non-ASCII, or longer than 32 bytes. kvmd has no
                such name, and the frame would be dropped without a word.
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        # kvmd acts on the flag only on a press, so on a release it is dead
        # weight. Dropping it here keeps the frame to the two values kvmd
        # reads, rather than sending a bit it will ignore.
        finish = finish and state
        if self._binary:
            flags = (0b01 if state else 0) | (0b10 if finish else 0)
            await self._send_bin(
                _OP_KEY, bytes([flags]) + _name_bytes(key, "Key"), "key"
            )
        else:
            event: dict[str, Any] = {"key": key, "state": state}
            if finish:
                # kvmd defaults it to False, so leaving it out is the same
                # event a client that never heard of the flag would send.
                event["finish"] = True
            await self._send_event("key", event)

    async def send_mouse_move(self, to_x: int, to_y: int) -> None:
        """Move the mouse to an absolute position.

        The coordinates are not pixels. kvmd works in a resolution-independent
        space from -32768 (left, top) to 32767 (right, bottom), so ``0, 0`` is
        the middle of the screen and ``send_mouse_move(500, 300)`` lands a
        hair right of and below it — not 500 pixels from the corner. Convert
        from pixels with ``round(x / (width - 1) * 65535) - 32768``.

        Values outside the range are clamped, by kvmd for a JSON event and
        here for a binary one, which has nowhere to put them.

        Args:
            to_x: Horizontal position, -32768 to 32767.
            to_y: Vertical position, -32768 to 32767.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        if self._binary:
            packed = struct.pack(
                ">hh",
                _clamp(to_x, _MOVE_MIN, _MOVE_MAX),
                _clamp(to_y, _MOVE_MIN, _MOVE_MAX),
            )
            await self._send_bin(_OP_MOUSE_MOVE, packed, "mouse_move")
        else:
            await self._send_event("mouse_move", {"to": {"x": to_x, "y": to_y}})

    async def send_mouse_button(self, button: MouseButton, state: bool) -> None:
        """Send a mouse button event.

        Args:
            button: Button name, one of
                ``aiopikvm.resources.hid.MouseButton``. A name kvmd does not
                know is dropped inside its handler with no answer of any
                kind, the way a bad key name is — there is no 400 on this
                socket to tell a typo from a click that landed.
            state: ``True`` for press, ``False`` for release.

        Raises:
            ConfigurationError: The button name cannot go into a binary frame,
                being empty, non-ASCII, or longer than 32 bytes. kvmd has no
                such name, and the frame would be dropped without a word.
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        if self._binary:
            flags = 0b01 if state else 0
            await self._send_bin(
                _OP_MOUSE_BUTTON,
                bytes([flags]) + _name_bytes(button, "Mouse button"),
                "mouse_button",
            )
        else:
            await self._send_event("mouse_button", {"button": button, "state": state})

    async def send_mouse_wheel(self, delta_x: int, delta_y: int) -> None:
        """Send a mouse wheel event.

        Deltas are steps in kvmd's own range, -127 to 127, clamped rather
        than rejected — by kvmd for a JSON event and here for a binary one,
        which has nowhere to put a larger number — and carried in the HID
        wheel field. They are not the browser's pixel deltas: a browser
        reports a scroll-down gesture as a positive ``deltaY``, and kvmd's own
        web UI negates it and sizes it by its scroll-rate setting (1 to 25, 5
        by default), so the gesture reaches the device as ``delta_y = -5``.

        Args:
            delta_x: Horizontal step, -127 to 127. It needs a backend with a
                horizontal wheel behind it, and in kvmd 4.206 only ``otg`` has
                one, while its ``horizontal_wheel`` option is on — the
                default. ``serial``, ``spi``, ``ch9329`` and ``bt`` drop it
                without a word, and which way a positive step pans is not
                settled here.
            delta_y: Vertical step, -127 to 127. Negative scrolls down on a
                host with the usual wheel mapping. ``ch9329`` keeps only the
                sign and sends one detent, a zero counting as negative, so the
                size is lost there.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_delta(_OP_MOUSE_WHEEL, "mouse_wheel", delta_x, delta_y)

    async def send_mouse_wheel_batch(
        self, deltas: Iterable[tuple[int, int]], *, squash: bool = False
    ) -> None:
        """Send several wheel steps in one frame.

        A step means what it does in ``send_mouse_wheel()``, backends and
        directions included.

        Args:
            deltas: ``(delta_x, delta_y)`` steps, in the order they happened.
                An empty batch is a frame kvmd does nothing with.
            squash: Ask kvmd to add the steps together instead of reporting
                each one. See
                [`send_mouse_relative_batch()`][aiopikvm.PiKVMWebSocket.send_mouse_relative_batch],
                which squashes by the same rule.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_deltas(_OP_MOUSE_WHEEL, "mouse_wheel", deltas, squash=squash)

    async def send_mouse_relative(self, delta_x: int, delta_y: int) -> None:
        """Move the mouse by an amount, rather than to a position.

        This needs the mouse in a relative mode: kvmd drops a relative event
        while the current mouse is absolute, and drops
        [`send_mouse_move()`][aiopikvm.PiKVMWebSocket.send_mouse_move] while
        it is relative — in both cases without a word to the sender.
        ``kvm.hid.set_params(mouse_output="usb_rel")`` switches it, and
        ``HIDState.mouse.absolute`` says which mode is on.

        Deltas are steps in kvmd's own range, -127 to 127, clamped rather than
        rejected — by kvmd for a JSON event and here for a binary one, which
        has nowhere to put a larger number. A gesture longer than one step
        therefore takes several events, which is what
        [`send_mouse_relative_batch()`][aiopikvm.PiKVMWebSocket.send_mouse_relative_batch]
        is for.

        Args:
            delta_x: Horizontal step, -127 to 127. Positive moves right.
            delta_y: Vertical step, -127 to 127. Positive moves down.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_delta(_OP_MOUSE_RELATIVE, "mouse_relative", delta_x, delta_y)

    async def send_mouse_relative_batch(
        self, deltas: Iterable[tuple[int, int]], *, squash: bool = False
    ) -> None:
        """Send several relative steps in one frame.

        One frame for a burst of movement is what kvmd's own web UI does: it
        collects the deltas a mouse produced between two screen refreshes and
        sends them together, rather than a frame per browser event.

        With *squash*, kvmd adds consecutive steps up instead of reporting
        each one, and starts a new sum whenever the running total would leave
        the -127 to 127 a HID report can carry. Fewer reports reach the host
        that way, at the cost of the shape of the path between them — and a
        batch that adds up to nothing sends nothing at all, since kvmd drops a
        final sum of ``(0, 0)``.

        Args:
            deltas: ``(delta_x, delta_y)`` steps, in the order they happened.
                An empty batch is a frame kvmd does nothing with.
            squash: Add the steps together where they fit into one report.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_deltas(
            _OP_MOUSE_RELATIVE, "mouse_relative", deltas, squash=squash
        )

    async def _send_delta(
        self, op: int, event_type: str, delta_x: int, delta_y: int
    ) -> None:
        """Send one step of a delta event.

        A single step keeps the shape kvmd's own web UI sends for one: a
        ``delta`` object rather than a list of one, and no squash flag, which
        means nothing for a step that has nothing to be added to.

        Args:
            op: kvmd operation number for the binary encoding.
            event_type: kvmd event name for the JSON encoding.
            delta_x: Horizontal step.
            delta_y: Vertical step.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        if self._binary:
            await self._send_bin(
                op, b"\x00" + _pack_delta(delta_x, delta_y), event_type
            )
        else:
            await self._send_event(event_type, {"delta": {"x": delta_x, "y": delta_y}})

    async def _send_deltas(
        self,
        op: int,
        event_type: str,
        deltas: Iterable[tuple[int, int]],
        *,
        squash: bool,
    ) -> None:
        """Send a batch of steps of a delta event.

        Args:
            op: kvmd operation number for the binary encoding.
            event_type: kvmd event name for the JSON encoding.
            deltas: The steps, in the order they happened.
            squash: Ask kvmd to add them together where they fit one report.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        steps = list(deltas)
        if self._binary:
            payload = bytes([0b01 if squash else 0]) + b"".join(
                _pack_delta(delta_x, delta_y) for (delta_x, delta_y) in steps
            )
            await self._send_bin(op, payload, event_type)
        else:
            await self._send_event(
                event_type,
                {
                    "delta": [
                        {"x": delta_x, "y": delta_y} for (delta_x, delta_y) in steps
                    ],
                    "squash": squash,
                },
            )


def _ws_url(url: str) -> str:
    """Turn a PiKVM base URL into the one a WebSocket connects to.

    Args:
        url: The base URL the client was built with.

    Returns:
        The same URL with a WebSocket scheme, no trailing path added.

    Raises:
        ConfigurationError: The scheme is neither ``https`` nor ``http``.
    """
    parsed = urlparse(url)
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme, "")
    if not scheme:
        raise ConfigurationError(
            f"Unsupported URL scheme {parsed.scheme!r}; the PiKVM URL must "
            "start with https:// or http://"
        )
    return urlunparse(parsed._replace(scheme=scheme))


def _credential_headers(
    auth: AuthMode,
    user: str,
    passwd: str | Callable[[], str],
    token: str,
) -> dict[str, str]:
    """Build the credential headers a WebSocket upgrade request carries.

    Shared by every socket this client opens: the kvmd event socket and the
    media socket go through the same auth chain a REST call does.

    Args:
        auth: Which credential to send.
        user: kvmd user name, for ``"headers"`` and ``"basic"``.
        passwd: Password, or a callable read at the moment of the handshake so
            that a rotating TOTP code is the one current then.
        token: Session token, for ``"cookie"``.

    Returns:
        The headers for that auth mode. The cookie goes in a plain ``Cookie``
        header — a WebSocket handshake is an ordinary HTTP GET, and there is
        no jar here to keep it in.
    """
    if auth == "cookie":
        return {"Cookie": f"auth_token={token}"}
    value = passwd() if callable(passwd) else passwd
    if auth == "basic":
        raw = f"{user}:{value}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}
    return {"X-KVMD-User": user, "X-KVMD-Passwd": value}


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge one event's payload into what the same subsystem said before.

    Nothing is changed in place: an object that was not in *update* is shared
    with *base* rather than copied, and one that was is rebuilt, so a snapshot
    already handed to a caller keeps saying what it said.

    Args:
        base: What is known about the subsystem so far.
        update: What the event carried.

    Returns:
        The two merged, *update* winning wherever they disagree.
    """
    merged = dict(base)
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


def _as_state(event_type: str, merged: dict[str, Any]) -> Any:
    """Turn a merged payload into whatever
    [`DeviceState`][aiopikvm.DeviceState] holds for it.

    Args:
        event_type: kvmd event name.
        merged: Everything that subsystem has sent, merged.

    Returns:
        The validated model.

    Raises:
        ResponseError: The payload does not match the model. Pydantic raises
            ``ValidationError``, which is outside the aiopikvm hierarchy and
            would escape ``except PiKVMError``.
    """
    (model, key) = _STATE_MODELS[event_type]
    data = merged.get(key) if key else merged
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ResponseError(
            f"The {event_type} WebSocket event adds up to a payload "
            f"{model.__name__} cannot parse. This usually means a kvmd "
            f"version aiopikvm does not know about yet:\n{exc}"
        ) from exc


def _pack_delta(delta_x: int, delta_y: int) -> bytes:
    """Pack one step the way kvmd's binary delta handlers unpack it.

    Args:
        delta_x: Horizontal step.
        delta_y: Vertical step.

    Returns:
        The pair as two signed bytes, clamped into the range that fits.
    """
    return struct.pack(
        ">bb",
        _clamp(delta_x, _DELTA_MIN, _DELTA_MAX),
        _clamp(delta_y, _DELTA_MIN, _DELTA_MAX),
    )


def _clamp(value: int, low: int, high: int) -> int:
    """Fit a value into the range kvmd's validator would have forced it into.

    Args:
        value: The value as the caller gave it.
        low: Lowest value kvmd accepts.
        high: Highest value kvmd accepts.

    Returns:
        The value, moved to the nearest end of the range if it was outside.
    """
    return min(max(low, value), high)


def _name_bytes(name: str, what: str) -> bytes:
    """Encode a key or button name for a binary frame.

    Args:
        name: The name as the caller gave it.
        what: What it names, for the error message.

    Returns:
        The name as the ASCII bytes kvmd decodes out of the frame.

    Raises:
        ConfigurationError: The name is empty, not ASCII, or longer than the
            32 bytes kvmd reads.
    """
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ConfigurationError(
            f"{what} name {name!r} is not ASCII; kvmd reads these names as "
            "ASCII out of the binary frame and has none like it"
        ) from exc
    if not 0 < len(encoded) <= _NAME_LIMIT:
        raise ConfigurationError(
            f"{what} name {name!r} does not fit a binary frame: kvmd reads "
            f"1 to {_NAME_LIMIT} bytes and has no name of that length"
        )
    return encoded


def _handshake_error(response: websockets.http11.Response) -> APIError:
    """Build the exception for an upgrade that was refused.

    kvmd refuses it with the same envelope it uses everywhere else, so the
    status goes through the same mapping the REST client uses. Anything but
    101 arrives here, a 2xx from something that ignored the upgrade included.

    Args:
        response: The HTTP response that came back instead of the upgrade.

    Returns:
        The exception to raise.
    """
    error, error_msg = _error_fields_from_bytes(response.body)
    # Not headers.get: websockets raises MultipleValuesError — a LookupError
    # rather than a KeyError, so Mapping.get does not absorb it — when a
    # header arrives twice, and that would escape PiKVMError entirely.
    location = response.headers.get_all("Location")
    return _status_error(
        response.status_code,
        error=error,
        error_msg=error_msg,
        detail=response.reason_phrase,
        location=location[0] if location else "",
    )
