"""WebSocket client for PiKVM — realtime events and HID input."""

import json
import logging
import ssl
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlparse, urlunparse

import websockets
import websockets.asyncio.client

from aiopikvm._exceptions import WebSocketError

logger = logging.getLogger(__name__)


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
        stream: int = 0,
        open_timeout: float = 10.0,
        close_timeout: float = 10.0,
    ) -> None:
        parsed = urlparse(url)
        scheme_map = {"https": "wss", "http": "ws"}
        ws_scheme = scheme_map.get(parsed.scheme, "")
        if not ws_scheme:
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
        ws_url = urlunparse(parsed._replace(scheme=ws_scheme))
        self._url = f"{ws_url}/api/ws?stream={stream}"
        self._user = user
        self._passwd = passwd
        self._verify_ssl = verify_ssl
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._connection: websockets.asyncio.client.ClientConnection | None = None

    async def __aenter__(self) -> Self:
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
            self._connection = await websockets.asyncio.client.connect(
                self._url,
                additional_headers=headers,
                ssl=ssl_context,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
            )
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            raise WebSocketError(f"Failed to connect: {exc}") from exc

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _ensure_connected(self) -> websockets.asyncio.client.ClientConnection:
        """Return the active connection or raise."""
        if self._connection is None:
            raise WebSocketError("Not connected")
        return self._connection

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Iterate over incoming events.

        Yields:
            Parsed JSON event dictionaries.
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
        except websockets.exceptions.ConnectionClosed:
            return

    async def send_key(self, key: str, *, state: bool) -> None:
        """Send a keyboard key event.

        Args:
            key: Key name.
            state: ``True`` for press, ``False`` for release.
        """
        conn = self._ensure_connected()
        event = {
            "event_type": "key",
            "event": {"key": key, "state": state},
        }
        await conn.send(json.dumps(event))

    async def send_mouse_move(self, to_x: int, to_y: int) -> None:
        """Move the mouse to absolute coordinates.

        Args:
            to_x: Target X coordinate.
            to_y: Target Y coordinate.
        """
        conn = self._ensure_connected()
        event = {
            "event_type": "mouse_move",
            "event": {"to": {"x": to_x, "y": to_y}},
        }
        await conn.send(json.dumps(event))

    async def send_mouse_button(self, button: str, state: bool) -> None:
        """Send a mouse button event.

        Args:
            button: Button name (e.g. ``"left"``).
            state: ``True`` for press, ``False`` for release.
        """
        conn = self._ensure_connected()
        event = {
            "event_type": "mouse_button",
            "event": {"button": button, "state": state},
        }
        await conn.send(json.dumps(event))

    async def send_mouse_wheel(self, delta_x: int, delta_y: int) -> None:
        """Send a mouse wheel event.

        Args:
            delta_x: Horizontal scroll delta.
            delta_y: Vertical scroll delta.
        """
        conn = self._ensure_connected()
        event = {
            "event_type": "mouse_wheel",
            "event": {"delta": {"x": delta_x, "y": delta_y}},
        }
        await conn.send(json.dumps(event))

    async def ping(self) -> None:
        """Send a ping event."""
        conn = self._ensure_connected()
        await conn.send(json.dumps({"event_type": "ping", "event": {}}))
