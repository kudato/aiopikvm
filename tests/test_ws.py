"""PiKVMWebSocket tests."""

import json
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import websockets.exceptions
from websockets.datastructures import Headers
from websockets.http11 import Response

from aiopikvm import (
    APIError,
    AuthError,
    ConfigurationError,
    PiKVMWebSocket,
    WebSocketError,
)
from tests.fixtures import load_json


def socket(url: str = "https://pikvm.local", **kwargs: Any) -> PiKVMWebSocket:
    """Build a client against a fake host."""
    return PiKVMWebSocket(url, user="admin", passwd="admin", **kwargs)


def connected(**kwargs: Any) -> tuple[PiKVMWebSocket, AsyncMock]:
    """Build a client with a mock connection already in place."""
    ws = socket(**kwargs)
    conn = AsyncMock()
    ws._connection = conn
    return (ws, conn)


def sent(conn: AsyncMock) -> dict[str, Any]:
    """Return the last frame handed to the connection."""
    frame: dict[str, Any] = json.loads(conn.send.call_args[0][0])
    return frame


def iterating(*messages: str | bytes, closed: BaseException | None = None) -> AsyncMock:
    """Build a mock connection that yields *messages*, then optionally raises."""
    conn = AsyncMock()

    async def _aiter(_: object) -> AsyncIterator[str | bytes]:
        for message in messages:
            yield message
        if closed is not None:
            raise closed

    conn.__aiter__ = _aiter
    return conn


def step(name: str) -> dict[str, Any]:
    """Return one recorded handshake refusal.

    Args:
        name: Step name from the ``ws_handshake`` scenario.

    Returns:
        The recorded step.

    Raises:
        KeyError: If the scenario has no such step.
    """
    steps = load_json("ws_handshake")["steps"]
    for recorded in steps:
        if recorded["name"] == name:
            return dict(recorded)
    known = ", ".join(recorded["name"] for recorded in steps)
    raise KeyError(f"Unknown ws_handshake step {name!r}; recorded: {known}")


def refusal(name: str) -> websockets.exceptions.InvalidStatus:
    """Rebuild the exception websockets raises for a recorded refusal.

    Args:
        name: Step name from the ``ws_handshake`` scenario.

    Returns:
        The ``InvalidStatus`` a real handshake against kvmd produces.
    """
    recorded = step(name)
    body = json.dumps(recorded["body"]).encode()
    return websockets.exceptions.InvalidStatus(
        Response(
            recorded["status"],
            recorded["reason_phrase"],
            Headers(
                {
                    "Content-Type": recorded["content_type"],
                    "Content-Length": str(len(body)),
                }
            ),
            body,
        )
    )


async def connect_failing(ws: PiKVMWebSocket, exc: BaseException) -> None:
    """Enter *ws* with the websockets handshake raising *exc*."""
    with patch("websockets.asyncio.client.connect", AsyncMock(side_effect=exc)):
        await ws.__aenter__()


# --- URL and parameters --------------------------------------------------


def test_stream_defaults_to_on_like_kvmd() -> None:
    """kvmd's own default is stream=true, and it keeps the streamer running."""
    assert socket()._url == "wss://pikvm.local/api/ws?stream=1"


def test_stream_off_is_sent_as_a_bool() -> None:
    """kvmd reads the flag with valid_bool; 0 is false, not "stream index 0"."""
    assert socket(stream=False)._url == "wss://pikvm.local/api/ws?stream=0"


def test_url_construction_http() -> None:
    assert socket("http://pikvm.local")._url == "ws://pikvm.local/api/ws?stream=1"


def test_unsupported_url_scheme() -> None:
    with pytest.raises(ConfigurationError, match="Unsupported URL scheme"):
        socket("ftp://pikvm.local")


def test_ws_timeouts_default() -> None:
    ws = socket()
    assert ws._open_timeout == 10.0
    assert ws._close_timeout == 10.0


def test_ws_timeouts_custom() -> None:
    ws = socket(open_timeout=5.0, close_timeout=3.0)
    assert ws._open_timeout == 5.0
    assert ws._close_timeout == 3.0


# --- Handshake -----------------------------------------------------------


async def test_aenter_wss_no_verify() -> None:
    ws = socket(verify_ssl=False)
    mock_connect = AsyncMock(return_value=AsyncMock())
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        ctx = mock_connect.call_args[1]["ssl"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
    ws._connection = None


async def test_aenter_wss_verify() -> None:
    ws = socket(verify_ssl=True)
    mock_connect = AsyncMock(return_value=AsyncMock())
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        assert mock_connect.call_args[1]["ssl"] is True
    ws._connection = None


async def test_aenter_http() -> None:
    ws = socket("http://pikvm.local")
    mock_connect = AsyncMock(return_value=AsyncMock())
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        assert mock_connect.call_args[1]["ssl"] is None
    ws._connection = None


async def test_aenter_sends_the_credential_headers() -> None:
    ws = PiKVMWebSocket("https://pikvm.local", user="operator", passwd="s3cret")
    mock_connect = AsyncMock(return_value=AsyncMock())
    with patch("websockets.asyncio.client.connect", mock_connect):
        await ws.__aenter__()
        headers = mock_connect.call_args[1]["additional_headers"]
    assert headers == {"X-KVMD-User": "operator", "X-KVMD-Passwd": "s3cret"}
    ws._connection = None


async def test_aenter_oserror() -> None:
    with pytest.raises(WebSocketError, match="Failed to connect"):
        await connect_failing(socket(), OSError("Connection refused"))


async def test_aenter_websocket_exception() -> None:
    with pytest.raises(WebSocketError, match="Failed to connect"):
        await connect_failing(socket(), websockets.exceptions.InvalidURI("bad", "why"))


@pytest.mark.parametrize("name", ["wrong_passwd", "unknown_user", "no_credentials"])
async def test_refused_credentials_raise_auth_error(name: str) -> None:
    """A refused upgrade reports like the HTTP client, not as a transport failure."""
    recorded = step(name)
    with pytest.raises(AuthError) as caught:
        await connect_failing(socket(), refusal(name))
    assert caught.value.status_code == recorded["status"]
    assert caught.value.error == recorded["body"]["result"]["error"]
    assert caught.value.error_msg == recorded["body"]["result"]["error_msg"]
    assert str(recorded["status"]) in str(caught.value)


async def test_refused_credentials_are_still_pikvm_errors() -> None:
    """`except APIError` keeps working for callers that do not want AuthError."""
    with pytest.raises(APIError):
        await connect_failing(socket(), refusal("wrong_passwd"))


async def test_rejected_query_is_not_reported_as_an_auth_failure() -> None:
    """kvmd's 400 for a bad stream flag is a plain APIError, never AuthError."""
    with pytest.raises(APIError) as caught:
        await connect_failing(socket(), refusal("bad_stream_value"))
    assert not isinstance(caught.value, AuthError)
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"
    assert "not a valid bool" in caught.value.error_msg


async def test_refusal_without_a_kvmd_envelope() -> None:
    """A proxy in front of kvmd answers with something else entirely."""
    body = b"<html>nginx</html>"
    response = Response(
        502,
        "Bad Gateway",
        Headers({"Content-Type": "text/html", "Content-Length": str(len(body))}),
        body,
    )
    with pytest.raises(APIError) as caught:
        await connect_failing(socket(), websockets.exceptions.InvalidStatus(response))
    assert caught.value.status_code == 502
    assert caught.value.error == ""
    assert "Bad Gateway" in str(caught.value)


async def test_refusal_without_a_body() -> None:
    """websockets leaves the body empty when the server sends none."""
    response = Response(403, "Forbidden", Headers(), b"")
    with pytest.raises(AuthError) as caught:
        await connect_failing(socket(), websockets.exceptions.InvalidStatus(response))
    assert caught.value.status_code == 403
    assert caught.value.error_msg == ""


# --- Events --------------------------------------------------------------


async def test_events_yields_parsed_frames() -> None:
    ws = socket()
    ws._connection = iterating(json.dumps({"event_type": "loop", "event": {}}))
    assert [event async for event in ws.events()] == [
        {"event_type": "loop", "event": {}}
    ]


async def test_events_binary_message() -> None:
    ws = socket()
    ws._connection = iterating(json.dumps({"event_type": "state", "data": {}}).encode())
    assert [event async for event in ws.events()] == [
        {"event_type": "state", "data": {}}
    ]


async def test_events_skips_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    ws = socket()
    ws._connection = iterating(
        "not valid json", json.dumps({"event_type": "state", "data": {}})
    )

    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        events = [event async for event in ws.events()]

    assert len(events) == 1
    assert events[0]["event_type"] == "state"
    assert "Skipping malformed WebSocket message" in caplog.text


async def test_events_end_on_a_clean_close() -> None:
    """websockets ends its own iteration when either side closes properly."""
    ws = socket()
    ws._connection = iterating(json.dumps({"event_type": "pong", "event": {}}))
    assert len([event async for event in ws.events()]) == 1


async def test_events_raise_when_the_connection_breaks() -> None:
    """A 1006 close reaches the caller instead of looking like end-of-stream."""
    ws = socket()
    ws._connection = iterating(
        json.dumps({"event_type": "pong", "event": {}}),
        closed=websockets.exceptions.ConnectionClosedError(None, None),
    )

    seen = []
    with pytest.raises(WebSocketError, match="Connection lost"):
        async for event in ws.events():
            seen.append(event)
    assert len(seen) == 1


async def test_events_require_a_connection() -> None:
    ws = socket()
    with pytest.raises(WebSocketError, match="Not connected"):
        await anext(ws.events())


# --- Sending -------------------------------------------------------------


async def test_not_connected_error() -> None:
    with pytest.raises(WebSocketError, match="Not connected"):
        await socket().send_key("KeyA", state=True)


async def test_send_key() -> None:
    ws, conn = connected()
    await ws.send_key("KeyA", state=True)
    assert sent(conn) == {"event_type": "key", "event": {"key": "KeyA", "state": True}}


async def test_send_mouse_move() -> None:
    ws, conn = connected()
    await ws.send_mouse_move(100, 200)
    assert sent(conn) == {
        "event_type": "mouse_move",
        "event": {"to": {"x": 100, "y": 200}},
    }


async def test_send_mouse_button() -> None:
    ws, conn = connected()
    await ws.send_mouse_button("left", True)
    assert sent(conn) == {
        "event_type": "mouse_button",
        "event": {"button": "left", "state": True},
    }


async def test_send_mouse_wheel() -> None:
    ws, conn = connected()
    await ws.send_mouse_wheel(0, -5)
    assert sent(conn) == {
        "event_type": "mouse_wheel",
        "event": {"delta": {"x": 0, "y": -5}},
    }


async def test_ping() -> None:
    ws, conn = connected()
    await ws.ping()
    assert sent(conn) == {"event_type": "ping", "event": {}}


async def test_send_on_a_broken_connection() -> None:
    """A dead socket must not leak the websockets exception to the caller."""
    ws, conn = connected()
    conn.send.side_effect = websockets.exceptions.ConnectionClosedError(None, None)
    with pytest.raises(WebSocketError, match="Failed to send 'key'"):
        await ws.send_key("KeyA", state=True)


# --- Lifecycle -----------------------------------------------------------


async def test_aexit_closes_connection() -> None:
    ws, conn = connected()
    await ws.__aexit__(None, None, None)
    conn.close.assert_awaited_once()
    assert ws._connection is None


async def test_aexit_forgets_a_connection_that_fails_to_close() -> None:
    ws, conn = connected()
    conn.close.side_effect = OSError("gone")
    with pytest.raises(OSError, match="gone"):
        await ws.__aexit__(None, None, None)
    assert ws._connection is None


async def test_aexit_none_connection() -> None:
    ws = socket()
    assert ws._connection is None
    await ws.__aexit__(None, None, None)
    assert ws._connection is None
