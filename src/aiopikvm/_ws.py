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
import dataclasses
import json
import logging
import ssl
import struct
from collections import deque
from collections.abc import AsyncIterator, Iterable
from types import TracebackType
from typing import Any, NamedTuple, Self
from urllib.parse import urlparse, urlunparse

import websockets
import websockets.asyncio.client
import websockets.http11
from pydantic import BaseModel, ValidationError

from aiopikvm._exceptions import (
    APIError,
    ConfigurationError,
    ResponseError,
    WebSocketError,
    _error_fields_from_bytes,
    _status_error,
)
from aiopikvm.models.atx import ATXState
from aiopikvm.models.gpio import GPIOState
from aiopikvm.models.hid import HIDKeymaps, HIDState
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
"""How many events :meth:`PiKVMWebSocket.ping` may buffer for :meth:`events`."""

_POLL_INTERVAL = 0.02
"""How often a waiting ping rechecks whether another reader took the socket."""


class KvmdVersion(NamedTuple):
    """The kvmd protocol version from the ``loop`` event.

    kvmd sends it as the first thing on every connection, and it is the only
    version signal the socket carries. Being a tuple, it compares the way a
    version should: ``ws.version >= (4, 100)``.

    Attributes:
        major: Major version, ``4`` for the kvmd 4.x series.
        minor: Minor version, e.g. ``186`` for kvmd 4.186.
    """

    major: int
    minor: int


@dataclasses.dataclass(frozen=True, slots=True)
class DeviceState:
    """Everything the socket has said about the device so far.

    One of these comes out of :meth:`PiKVMWebSocket.states` per event that
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
            one key at a time — ``uptime``, ``health``, ``system`` — and this
            is still a raw dictionary; typing it is tracked in #71.
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
    info: dict[str, Any] = dataclasses.field(default_factory=dict)


_STATE_MODELS: dict[str, tuple[type[BaseModel], str]] = {
    "atx": (ATXState, ""),
    "gpio": (GPIOState, ""),
    "hid": (HIDState, ""),
    "hid_keymaps": (HIDKeymaps, "keymaps"),
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
        ssl_context: ssl.SSLContext | bool | None,
        open_timeout: float,
        close_timeout: float,
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
            open_timeout: Seconds to wait for the handshake.
            close_timeout: Seconds to wait for the closing handshake.
        """
        self._follow_redirects = follow_redirects
        super().__init__(
            uri,
            additional_headers=additional_headers,
            ssl=ssl_context,
            open_timeout=open_timeout,
            close_timeout=close_timeout,
        )

    def process_redirect(self, exc: Exception) -> Exception | str:
        """Decide what to do with a handshake response.

        Args:
            exc: The exception the handshake produced.

        Returns:
            The URI to follow when redirects are allowed and this is one,
            otherwise the exception, which makes *websockets* raise it — and
            which :meth:`PiKVMWebSocket.__aenter__` turns into a
            :class:`PiKVMError`.
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

    Usage::

        async with kvm.ws() as ws:
            async for event in ws.events():
                print(event)
    """

    def __init__(
        self,
        url: str,
        *,
        user: str,
        passwd: str,
        verify_ssl: bool = True,
        stream: bool = True,
        binary: bool = False,
        follow_redirects: bool = False,
        open_timeout: float = 10.0,
        close_timeout: float = 10.0,
    ) -> None:
        """Prepare a connection.

        Args:
            url: PiKVM base URL, ``https://`` or ``http://``.
            user: kvmd user name.
            passwd: Password, TOTP code appended if the device asks for one.
            verify_ssl: Verify the TLS certificate.
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
                was verified against kvmd 4.186.
            follow_redirects: Follow a redirected handshake instead of
                raising :class:`RedirectError`. Off by default: the upgrade
                carries the password in a header, and following the redirect
                hands it to whatever the redirect points at.
            open_timeout: Seconds to wait for the handshake.
            close_timeout: Seconds to wait for the closing handshake.

        Raises:
            ConfigurationError: If the URL scheme is not ``https`` or ``http``.
        """
        parsed = urlparse(url)
        scheme_map = {"https": "wss", "http": "ws"}
        ws_scheme = scheme_map.get(parsed.scheme, "")
        if not ws_scheme:
            raise ConfigurationError(
                f"Unsupported URL scheme {parsed.scheme!r}; the PiKVM URL must "
                "start with https:// or http://"
            )
        ws_url = urlunparse(parsed._replace(scheme=ws_scheme))
        # kvmd reads the flag with valid_bool, which takes 1/true/yes and
        # 0/false/no and answers 400 to anything else.
        self._url = f"{ws_url}/api/ws?stream={'1' if stream else '0'}"
        self._user = user
        self._passwd = passwd
        self._verify_ssl = verify_ssl
        self._binary = binary
        self._follow_redirects = follow_redirects
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._connection: websockets.asyncio.client.ClientConnection | None = None
        self._version: KvmdVersion | None = None
        # Only one task may read the socket at a time, and whichever one holds
        # the lock routes what it reads for everybody: an event another task
        # buffered is still an event this connection received.
        self._read_lock = asyncio.Lock()
        self._pending: deque[dict[str, Any]] = deque()
        self._overflowed = False
        self._pong_waiters: list[asyncio.Future[float]] = []

    async def __aenter__(self) -> Self:
        """Open the connection.

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
        ssl_context: ssl.SSLContext | bool | None = None
        if self._url.startswith("wss://"):
            if not self._verify_ssl:
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ssl_context = True

        headers = {
            "X-KVMD-User": self._user,
            "X-KVMD-Passwd": self._passwd,
        }

        try:
            self._connection = await _Connector(
                self._url,
                additional_headers=headers,
                ssl_context=ssl_context,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                follow_redirects=self._follow_redirects,
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
        self._overflowed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the connection, whatever happened inside the block.

        A :meth:`ping` still waiting for its answer fails here rather than
        waiting out its timeout: the socket it was waiting on is gone.

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

    @property
    def version(self) -> KvmdVersion | None:
        """The kvmd version this connection reported, once it has been read.

        kvmd sends the ``loop`` event carrying it before anything else, so
        this is set as soon as one frame has been read off the socket —
        by :meth:`events`, or by a :meth:`ping` waiting for its answer. It is
        ``None`` before that, and on a connection whose ``loop`` event carried
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
        answer to :meth:`ping`, which that method consumes; anything else on
        that channel is logged and dropped, since a binary frame is an
        operation number and a payload, not an event.

        The iteration ends when either side closes the connection cleanly. A
        connection that breaks instead — the device rebooting, the network
        going away, kvmd restarting — raises, because a caller that only sees
        the loop finish cannot tell "kvmd has nothing more to say" from
        "the events stopped arriving".

        Yields:
            Parsed JSON event dictionaries.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke instead of closing cleanly.
        """
        while True:
            while self._pending:
                yield self._pending.popleft()
            # The lock is held for one frame at a time, so a ping in another
            # task gets its turn between events instead of after the last one.
            async with self._read_lock:
                try:
                    event = await self._read_one()
                except _Finished:
                    return
            if event is not None:
                yield event

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
        carries is on :attr:`version`.

        Everything :meth:`events` does about the connection applies here, and
        the two cannot be iterated over the same socket at once: this is
        :meth:`events` with the states built on top.

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
            elif event_type == "info" or event_type in _STATE_MODELS:
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
        that something on the other end still holds a TCP socket open.
        Keeping the connection alive needs neither — *websockets* sends a
        protocol ping every 20 seconds by itself and drops the connection when
        one goes unanswered for another 20, which is what turns a silently
        dead link into a :class:`WebSocketError` out of :meth:`events`.

        The answer arrives on the socket like everything else, so this waits
        for whoever is reading it. When :meth:`events` is being iterated in
        another task, that iteration hands the pong over; otherwise this reads
        the socket itself and keeps the events it finds on the way for the
        next :meth:`events` call. Either way the round trip is measured from
        the frame going out to the pong being read, so a consumer of
        :meth:`events` that takes its time between frames adds its own delay to
        the number — the pong waits behind whatever it is doing.

        Args:
            timeout: Seconds to wait for the answer.

        Returns:
            The round trip in seconds.

        Raises:
            WebSocketError: The client is not connected, the connection broke
                or closed before the answer arrived, or kvmd did not answer
                within *timeout*.
        """
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
                await self._wait_pong(waiter)
            return waiter.result() - sent_at
        except TimeoutError as exc:
            raise WebSocketError(
                f"kvmd did not answer the ping within {timeout} s"
            ) from exc
        finally:
            if waiter in self._pong_waiters:
                self._pong_waiters.remove(waiter)

    async def _wait_pong(self, waiter: asyncio.Future[float]) -> None:
        """Read the socket, or wait for whoever is reading it, until *waiter*.

        Args:
            waiter: Future resolved with the moment a pong was read.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke or closed before the answer arrived.
        """
        while not waiter.done():
            if self._read_lock.locked():
                # Another task is reading; it resolves the waiter when the
                # pong turns up. The poll only exists for the case where that
                # task stops reading without ever seeing one.
                await asyncio.wait([waiter], timeout=_POLL_INTERVAL)
                continue
            async with self._read_lock:
                if waiter.done():
                    return
                try:
                    event = await self._read_one()
                except _Finished as exc:
                    raise WebSocketError(
                        "The connection closed before kvmd answered the ping"
                    ) from exc
                if event is not None:
                    self._buffer(event)

    def _buffer(self, event: dict[str, Any]) -> None:
        """Keep an event read while waiting for something else.

        The buffer is bounded, because a caller that only ever pings never
        collects what those pings read: kvmd broadcasts its state whether or
        not anyone iterates :meth:`events`.

        Args:
            event: The event to hand to the next :meth:`events` call.
        """
        if len(self._pending) >= _PENDING_LIMIT:
            if not self._overflowed:
                logger.warning(
                    "Dropping WebSocket events: %d are buffered and nothing "
                    "is reading events()",
                    _PENDING_LIMIT,
                )
                self._overflowed = True
            self._pending.popleft()
        self._pending.append(event)

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
                have the keys it exempts. It needs kvmd 4.33, and over the
                binary channel an older one does worse than ignore it: it
                validates the whole flags byte as a boolean, so a frame
                carrying bit 1 fails that check and is thrown away entire —
                the press never happens, and nothing comes back to say so.

        Raises:
            ConfigurationError: The key name cannot go into a binary frame,
                being empty, non-ASCII, or longer than 32 bytes. kvmd has no
                such name, and the frame would be dropped without a word.
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        # kvmd acts on the flag only on a press. On a release it is dead
        # weight in every version, and worse than that over the binary
        # channel: kvmd 4.32 and older read the whole flags byte as a
        # boolean and drop a frame whose byte is neither 0 nor 1, so the
        # release would never arrive and the key would stay down — the very
        # failure the flag is for.
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
                each one. See :meth:`send_mouse_relative_batch`, which squashes
                by the same rule.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_deltas(_OP_MOUSE_WHEEL, "mouse_wheel", deltas, squash=squash)

    async def send_mouse_relative(self, delta_x: int, delta_y: int) -> None:
        """Move the mouse by an amount, rather than to a position.

        This needs the mouse in a relative mode: kvmd drops a relative event
        while the current mouse is absolute, and drops
        :meth:`send_mouse_move` while it is relative — in both cases without
        a word to the sender. ``kvm.hid.set_params(mouse_output="usb_rel")``
        switches it, and ``HIDState.mouse.absolute`` says which mode is on.

        Deltas are steps in kvmd's own range, -127 to 127, clamped rather than
        rejected — by kvmd for a JSON event and here for a binary one, which
        has nowhere to put a larger number. A gesture longer than one step
        therefore takes several events, which is what
        :meth:`send_mouse_relative_batch` is for.

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
    """Turn a merged payload into whatever :class:`DeviceState` holds for it.

    Args:
        event_type: kvmd event name.
        merged: Everything that subsystem has sent, merged.

    Returns:
        The validated model, or the payload itself for ``info``, which has no
        model yet.

    Raises:
        ResponseError: The payload does not match the model. Pydantic raises
            ``ValidationError``, which is outside the aiopikvm hierarchy and
            would escape ``except PiKVMError``.
    """
    if event_type not in _STATE_MODELS:
        return merged
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
