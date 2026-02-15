"""PiKVMWebSocket tests."""

import json
import logging
import ssl
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import websockets.exceptions

from aiopikvm import PiKVMWebSocket, WebSocketError


def test_url_construction() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    assert ws._url == "wss://pikvm.local/api/ws?stream=0"


def test_url_construction_http() -> None:
    ws = PiKVMWebSocket("http://pikvm.local", user="admin", passwd="admin")
    assert ws._url == "ws://pikvm.local/api/ws?stream=0"


def test_url_construction_custom_stream() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin", stream=1)
    assert ws._url == "wss://pikvm.local/api/ws?stream=1"


async def test_not_connected_error() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    with pytest.raises(WebSocketError, match="Not connected"):
        await ws.send_key("KeyA", state=True)


async def test_send_key() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()
    ws._connection = mock_conn

    await ws.send_key("KeyA", state=True)
    sent = json.loads(mock_conn.send.call_args[0][0])
    assert sent["event_type"] == "key"
    assert sent["event"]["key"] == "KeyA"
    assert sent["event"]["state"] is True


async def test_send_mouse_move() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()
    ws._connection = mock_conn

    await ws.send_mouse_move(100, 200)
    sent = json.loads(mock_conn.send.call_args[0][0])
    assert sent["event_type"] == "mouse_move"
    assert sent["event"]["to"]["x"] == 100
    assert sent["event"]["to"]["y"] == 200


async def test_send_mouse_button() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()
    ws._connection = mock_conn

    await ws.send_mouse_button("left", True)
    sent = json.loads(mock_conn.send.call_args[0][0])
    assert sent["event_type"] == "mouse_button"
    assert sent["event"]["button"] == "left"


async def test_send_mouse_wheel() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()
    ws._connection = mock_conn

    await ws.send_mouse_wheel(0, -5)
    sent = json.loads(mock_conn.send.call_args[0][0])
    assert sent["event_type"] == "mouse_wheel"
    assert sent["event"]["delta"]["y"] == -5


async def test_ping() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()
    ws._connection = mock_conn

    await ws.ping()
    sent = json.loads(mock_conn.send.call_args[0][0])
    assert sent["event_type"] == "ping"


def test_unsupported_url_scheme() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        PiKVMWebSocket("ftp://pikvm.local", user="admin", passwd="admin")


def test_ws_timeouts_default() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    assert ws._open_timeout == 10.0
    assert ws._close_timeout == 10.0


def test_ws_timeouts_custom() -> None:
    ws = PiKVMWebSocket(
        "https://pikvm.local",
        user="admin",
        passwd="admin",
        open_timeout=5.0,
        close_timeout=3.0,
    )
    assert ws._open_timeout == 5.0
    assert ws._close_timeout == 3.0


async def test_events_skips_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()

    valid_msg = json.dumps({"event_type": "state", "data": {}})
    messages = ["not valid json", valid_msg]

    async def _aiter(_: object) -> AsyncIterator[str]:
        for msg in messages:
            yield msg

    mock_conn.__aiter__ = _aiter
    ws._connection = mock_conn

    events = []
    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        async for event in ws.events():
            events.append(event)

    assert len(events) == 1
    assert events[0]["event_type"] == "state"
    assert "Skipping malformed WebSocket message" in caplog.text


async def test_aenter_wss_no_verify() -> None:
    ws = PiKVMWebSocket(
        "https://pikvm.local", user="admin", passwd="admin", verify_ssl=False
    )
    mock_conn = AsyncMock()
    mock_connect = AsyncMock(return_value=mock_conn)
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        call_kwargs = mock_connect.call_args[1]
        ctx = call_kwargs["ssl"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
    ws._connection = None


async def test_aenter_wss_verify() -> None:
    ws = PiKVMWebSocket(
        "https://pikvm.local", user="admin", passwd="admin", verify_ssl=True
    )
    mock_conn = AsyncMock()
    mock_connect = AsyncMock(return_value=mock_conn)
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["ssl"] is True
    ws._connection = None


async def test_aenter_http() -> None:
    ws = PiKVMWebSocket(
        "http://pikvm.local", user="admin", passwd="admin", verify_ssl=False
    )
    mock_conn = AsyncMock()
    mock_connect = AsyncMock(return_value=mock_conn)
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["ssl"] is None
    ws._connection = None


async def test_aenter_oserror() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_connect = AsyncMock(side_effect=OSError("Connection refused"))
    with (
        patch("websockets.asyncio.client.connect", mock_connect),
        pytest.raises(WebSocketError, match="Failed to connect"),
    ):
        await ws.__aenter__()


async def test_aenter_websocket_exception() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_connect = AsyncMock(
        side_effect=websockets.exceptions.InvalidURI("bad", "bad uri"),
    )
    with (
        patch("websockets.asyncio.client.connect", mock_connect),
        pytest.raises(WebSocketError, match="Failed to connect"),
    ):
        await ws.__aenter__()


async def test_aexit_closes_connection() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()
    ws._connection = mock_conn
    await ws.__aexit__(None, None, None)
    mock_conn.close.assert_awaited_once()
    assert ws._connection is None


async def test_aexit_none_connection() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    assert ws._connection is None
    await ws.__aexit__(None, None, None)
    assert ws._connection is None


async def test_events_binary_message() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()

    binary_msg = json.dumps({"event_type": "state", "data": {}}).encode()

    async def _aiter(_: object) -> AsyncIterator[bytes]:
        yield binary_msg

    mock_conn.__aiter__ = _aiter
    ws._connection = mock_conn

    events = []
    async for event in ws.events():
        events.append(event)

    assert len(events) == 1
    assert events[0]["event_type"] == "state"


async def test_events_connection_closed() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="admin", passwd="admin")
    mock_conn = AsyncMock()

    async def _aiter(_: object) -> AsyncIterator[str]:
        yield json.dumps({"event_type": "ping"})
        raise websockets.exceptions.ConnectionClosed(None, None)

    mock_conn.__aiter__ = _aiter
    ws._connection = mock_conn

    events = []
    async for event in ws.events():
        events.append(event)

    assert len(events) == 1
    assert events[0]["event_type"] == "ping"
