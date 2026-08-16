"""WebSocket client for PiKVM — realtime events and HID input.

kvmd exposes one socket, ``GET /api/ws``. It carries the event stream every
subsystem broadcasts its state on, and it takes HID input in the other
direction. The upgrade goes through the same auth chain as the REST API, so a
refused handshake arrives as a plain HTTP response and is reported with the
same exceptions.
"""

import json
import logging
import ssl
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlparse, urlunparse

import websockets
import websockets.asyncio.client
import websockets.http11

from aiopikvm._exceptions import (
    APIError,
    ConfigurationError,
    WebSocketError,
    _error_fields_from_bytes,
    _status_error,
)

logger = logging.getLogger(__name__)


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
        self._follow_redirects = follow_redirects
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._connection: websockets.asyncio.client.ClientConnection | None = None

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

        return self

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

    def _ensure_connected(self) -> websockets.asyncio.client.ClientConnection:
        """Return the active connection or raise."""
        if self._connection is None:
            raise WebSocketError("Not connected")
        return self._connection

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Iterate over incoming events.

        Every frame is a ``{"event_type": ..., "event": ...}`` object. The
        first one is always ``loop``, carrying the kvmd version; after it
        each subsystem sends its current state once, interleaved with the
        broadcasts other clients trigger, so nothing but ``loop`` arrives in
        a guaranteed order. See the WebSocket guide for the full list.

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
        conn = self._ensure_connected()
        try:
            async for message in conn:
                try:
                    if isinstance(message, str):
                        yield json.loads(message)
                    else:
                        yield json.loads(message.decode())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    logger.warning("Skipping malformed WebSocket message: %s", exc)
        except websockets.exceptions.ConnectionClosed as exc:
            # websockets ends the iteration itself on a clean close, so
            # anything arriving here is a broken connection.
            raise WebSocketError(
                f"Connection lost while reading events: {exc}"
            ) from exc

    async def _send_event(self, event_type: str, event: dict[str, Any]) -> None:
        """Send one event frame.

        Args:
            event_type: kvmd event name.
            event: Payload for that event.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        conn = self._ensure_connected()
        try:
            await conn.send(json.dumps({"event_type": event_type, "event": event}))
        except websockets.exceptions.WebSocketException as exc:
            raise WebSocketError(f"Failed to send {event_type!r}: {exc}") from exc

    async def send_key(self, key: str, *, state: bool) -> None:
        """Send a keyboard key event.

        Args:
            key: Key name, one of kvmd's web names such as ``"KeyA"`` or
                ``"ControlLeft"``. kvmd ignores an event it cannot map.
            state: ``True`` for press, ``False`` for release. kvmd holds the
                key until the release arrives.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_event("key", {"key": key, "state": state})

    async def send_mouse_move(self, to_x: int, to_y: int) -> None:
        """Move the mouse to an absolute position.

        The coordinates are not pixels. kvmd works in a resolution-independent
        space from -32768 (left, top) to 32767 (right, bottom), so ``0, 0`` is
        the middle of the screen and ``send_mouse_move(500, 300)`` lands a
        hair right of and below it — not 500 pixels from the corner. Convert
        from pixels with ``round(x / (width - 1) * 65535) - 32768``.

        Values outside the range are clamped by kvmd, not rejected.

        Args:
            to_x: Horizontal position, -32768 to 32767.
            to_y: Vertical position, -32768 to 32767.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_event("mouse_move", {"to": {"x": to_x, "y": to_y}})

    async def send_mouse_button(self, button: str, state: bool) -> None:
        """Send a mouse button event.

        Args:
            button: One of ``"left"``, ``"right"``, ``"middle"``, ``"up"``
                (browser back) or ``"down"`` (browser forward).
            state: ``True`` for press, ``False`` for release.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_event("mouse_button", {"button": button, "state": state})

    async def send_mouse_wheel(self, delta_x: int, delta_y: int) -> None:
        """Send a mouse wheel event.

        Deltas are steps in kvmd's own range, -127 to 127, clamped rather
        than rejected, and passed straight into the USB HID wheel field.
        They are not the browser's pixel deltas: kvmd's own web UI sends one
        step per gesture, sized by its scroll-rate setting (1 to 25,
        5 by default) and negated, so a scroll-down gesture leaves it as
        ``delta_y = -5``.

        Args:
            delta_x: Horizontal step, -127 to 127.
            delta_y: Vertical step, -127 to 127. Negative scrolls down on a
                host with the usual wheel mapping.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_event("mouse_wheel", {"delta": {"x": delta_x, "y": delta_y}})

    async def ping(self) -> None:
        """Ask kvmd for a ``pong`` event.

        This is kvmd's application-level ping, not the protocol one — the
        answer arrives through :meth:`events` like any other frame, and this
        call does not wait for it. Keeping the socket alive needs neither:
        *websockets* sends a protocol ping every 20 seconds by itself and
        drops the connection when one goes unanswered for another 20, which
        is what turns a silently dead link into a :class:`WebSocketError`
        out of :meth:`events`.

        Raises:
            WebSocketError: The client is not connected, or the connection
                broke before the frame could be sent.
        """
        await self._send_event("ping", {})


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
