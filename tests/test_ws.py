"""PiKVMWebSocket tests."""

import asyncio
import json
import logging
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import websockets.asyncio.server
import websockets.exceptions
import websockets.http11
from websockets.datastructures import Headers
from websockets.uri import parse_uri

from aiopikvm import (
    APIError,
    AuthError,
    BusyError,
    ConfigurationError,
    PiKVMWebSocket,
    RedirectError,
    UnavailableError,
    WebSocketError,
)
from aiopikvm._ws import _Connector
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


async def connect_failing(ws: PiKVMWebSocket, exc: BaseException) -> None:
    """Enter *ws* with the websockets handshake raising *exc*."""
    with patch("aiopikvm._ws._Connector", AsyncMock(side_effect=exc)):
        await ws.__aenter__()


def response(
    status: int, reason: str, body: bytes = b"", **headers: str
) -> websockets.http11.Response:
    """Build the HTTP response a server rejects the upgrade with."""
    sent = {"Content-Length": str(len(body)), **headers}
    return websockets.http11.Response(status, reason, Headers(sent), body)


def recorded_response(name: str) -> websockets.http11.Response:
    """Build the response kvmd was recorded refusing the upgrade with."""
    step_data = step(name)
    body = json.dumps(step_data["body"]).encode()
    return response(
        step_data["status"],
        step_data["reason_phrase"],
        body,
        **{"Content-Type": step_data["content_type"]},
    )


Handler = Callable[[websockets.asyncio.server.ServerConnection], Awaitable[None]]


async def _hold(connection: websockets.asyncio.server.ServerConnection) -> None:
    """Keep the connection open until the client is done with it."""
    await connection.wait_closed()


@asynccontextmanager
async def serving(
    reject: websockets.http11.Response | None = None,
    handler: Handler = _hold,
) -> AsyncIterator[tuple[str, list[websockets.http11.Request]]]:
    """Run a WebSocket server on loopback.

    Nothing here stands in for kvmd's payloads — the server exists so the
    real *websockets* client runs a real handshake and a real close, instead
    of a mock guessing what either would have done.

    Args:
        reject: Response to refuse the upgrade with; accept it when ``None``.
        handler: What the server does with an accepted connection.

    Yields:
        The server's URL and the list its received requests accumulate in.
    """
    seen: list[websockets.http11.Request] = []

    async def process(
        connection: websockets.asyncio.server.ServerConnection,
        request: websockets.http11.Request,
    ) -> websockets.http11.Response | None:
        seen.append(request)
        return reject

    async with websockets.asyncio.server.serve(
        handler, "127.0.0.1", 0, process_request=process
    ) as server:
        host, port = server.sockets[0].getsockname()[:2]
        yield (f"http://{host}:{port}", seen)


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
    with patch("aiopikvm._ws._Connector", mock_connect):
        await ws.__aenter__()
        ctx = mock_connect.call_args[1]["ssl_context"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
    ws._connection = None


async def test_aenter_wss_verify() -> None:
    ws = socket(verify_ssl=True)
    mock_connect = AsyncMock(return_value=AsyncMock())
    with patch("aiopikvm._ws._Connector", mock_connect):
        await ws.__aenter__()
        assert mock_connect.call_args[1]["ssl_context"] is True
    ws._connection = None


async def test_aenter_http() -> None:
    ws = socket("http://pikvm.local")
    mock_connect = AsyncMock(return_value=AsyncMock())
    with patch("aiopikvm._ws._Connector", mock_connect):
        await ws.__aenter__()
        assert mock_connect.call_args[1]["ssl_context"] is None
    ws._connection = None


async def test_connector_forwards_what_it_was_given() -> None:
    """The three tests above patch _Connector away; this pins what it passes on."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = _Connector(
        "wss://pikvm.local/api/ws?stream=1",
        additional_headers={"X-KVMD-User": "admin"},
        ssl_context=context,
        open_timeout=7.0,
        close_timeout=3.0,
    )
    assert connector.open_timeout == 7.0
    assert connector.connection_kwargs["ssl"] is context
    assert connector.additional_headers == {"X-KVMD-User": "admin"}
    # close_timeout only reaches the connection websockets builds per attempt.
    built = connector.protocol_factory(parse_uri(connector.uri))
    assert built.close_timeout == 3.0


async def test_aenter_oserror() -> None:
    with pytest.raises(WebSocketError, match="Failed to connect"):
        await connect_failing(socket(), OSError("Connection refused"))


async def test_aenter_websocket_exception() -> None:
    with pytest.raises(WebSocketError, match="Failed to connect"):
        await connect_failing(socket(), websockets.exceptions.InvalidURI("bad", "why"))


async def test_aenter_value_error() -> None:
    """websockets rejects some URIs with a bare ValueError, outside our hierarchy."""
    with pytest.raises(WebSocketError, match="Failed to connect"):
        await connect_failing(socket(), ValueError("ssl=None is incompatible"))


# --- Handshake against a real server -------------------------------------


async def test_handshake_sends_the_credential_headers() -> None:
    """The upgrade carries the credentials kvmd's auth chain reads."""
    async with serving() as (url, seen):
        async with PiKVMWebSocket(url, user="operator", passwd="s3cret"):
            pass
    assert seen[0].headers["X-KVMD-User"] == "operator"
    assert seen[0].headers["X-KVMD-Passwd"] == "s3cret"


async def test_handshake_asks_for_the_stream() -> None:
    """The flag reaches the server as the bool kvmd's validator reads."""
    async with serving() as (url, seen):
        async with socket(url):
            pass
        async with socket(url, stream=False):
            pass
    assert [request.path for request in seen] == [
        "/api/ws?stream=1",
        "/api/ws?stream=0",
    ]


@pytest.mark.parametrize("name", ["wrong_passwd", "unknown_user", "no_credentials"])
async def test_refused_credentials_raise_auth_error(name: str) -> None:
    """A refused upgrade reports like the HTTP client, not as a transport failure."""
    recorded = step(name)
    async with serving(recorded_response(name)) as (url, _):
        with pytest.raises(AuthError) as caught:
            async with socket(url):
                pass
    assert caught.value.status_code == recorded["status"]
    assert caught.value.error == recorded["body"]["result"]["error"]
    assert caught.value.error_msg == recorded["body"]["result"]["error_msg"]
    assert str(recorded["status"]) in str(caught.value)


async def test_refused_credentials_are_still_pikvm_errors() -> None:
    """`except APIError` keeps working for callers that do not want AuthError."""
    async with serving(recorded_response("wrong_passwd")) as (url, _):
        with pytest.raises(APIError):
            async with socket(url):
                pass


async def test_rejected_query_is_not_reported_as_an_auth_failure() -> None:
    """kvmd's 400 for a bad stream flag is a plain APIError, never AuthError."""
    async with serving(recorded_response("bad_stream_value")) as (url, _):
        with pytest.raises(APIError) as caught:
            async with socket(url):
                pass
    assert not isinstance(caught.value, AuthError)
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"
    assert "not a valid bool" in caught.value.error_msg


@pytest.mark.parametrize(
    ("status", "expected"),
    [(409, BusyError), (503, UnavailableError), (502, APIError)],
)
async def test_status_mapping_matches_the_http_client(
    status: int, expected: type[APIError]
) -> None:
    """A status means the same thing whichever transport reported it."""
    async with serving(response(status, "Nope")) as (url, _):
        with pytest.raises(expected) as caught:
            async with socket(url):
                pass
    assert type(caught.value) is expected
    assert caught.value.status_code == status


async def test_refusal_without_a_kvmd_envelope() -> None:
    """A proxy in front of kvmd answers with something else entirely."""
    body = b"<html>nginx</html>"
    refused = response(502, "Bad Gateway", body, **{"Content-Type": "text/html"})
    async with serving(refused) as (url, _):
        with pytest.raises(APIError) as caught:
            async with socket(url):
                pass
    assert caught.value.status_code == 502
    assert caught.value.error == ""
    assert "Bad Gateway" in str(caught.value)


async def test_refusal_with_a_json_body_that_is_not_an_envelope() -> None:
    """Valid JSON without kvmd's result block leaves the error fields empty."""
    async with serving(response(500, "Oops", b'{"error": "boom"}')) as (url, _):
        with pytest.raises(APIError) as caught:
            async with socket(url):
                pass
    assert caught.value.error == ""
    assert caught.value.error_msg == ""


async def test_redirect_is_reported_instead_of_followed() -> None:
    """Following it would hand the password to whatever the redirect points at."""
    async with serving() as (target, target_seen):
        location = f"ws{target[4:]}/api/ws"
        async with serving(response(302, "Found", Location=location)) as (url, _):
            with pytest.raises(RedirectError) as caught:
                async with socket(url):
                    pass
    assert caught.value.status_code == 302
    assert location in str(caught.value), "the caller needs to know where to point"
    assert "follow_redirects=True" in str(caught.value)
    assert target_seen == [], "the credentials must not reach the redirect target"


async def test_redirect_is_followed_when_asked() -> None:
    """The opt-out matches the HTTP client's follow_redirects."""
    async with serving() as (target, target_seen):
        moved = response(302, "Found", Location=f"ws{target[4:]}/api/ws")
        async with serving(moved) as (url, _):
            async with socket(url, follow_redirects=True):
                pass
    assert len(target_seen) == 1


@pytest.mark.parametrize("follow", [False, True])
async def test_redirect_repeated_header_stays_in_the_hierarchy(follow: bool) -> None:
    """websockets' own lookup raises a LookupError that is not a KeyError.

    Both halves have to be covered: reading the header to report the
    redirect, and reading it to follow one.
    """
    headers = Headers()
    headers["Location"] = "ws://one.invalid/api/ws"
    headers["Location"] = "ws://two.invalid/api/ws"
    headers["Content-Length"] = "0"
    duplicated = websockets.http11.Response(302, "Found", headers, b"")
    async with serving(duplicated) as (url, _):
        with pytest.raises(RedirectError) as caught:
            async with socket(url, follow_redirects=follow):
                pass
    assert "one.invalid" in str(caught.value)


@pytest.mark.parametrize(
    ("status", "redirect"),
    [(200, False), (299, False), (300, True), (399, True), (400, False)],
)
async def test_only_3xx_is_a_redirect(status: int, redirect: bool) -> None:
    """Anything but 101 arrives here, and a 200 page is not a redirect."""
    async with serving(response(status, "Whatever", b"<html>portal</html>")) as (
        url,
        _,
    ):
        with pytest.raises(APIError) as caught:
            async with socket(url):
                pass
    assert isinstance(caught.value, RedirectError) is redirect
    assert caught.value.status_code == status


async def test_refusal_with_a_body_that_is_not_utf8() -> None:
    """A binary body must not become a UnicodeDecodeError at the caller."""
    async with serving(response(500, "Oops", b"\xff\xfe\x00garbage")) as (url, _):
        with pytest.raises(APIError) as caught:
            async with socket(url):
                pass
    assert caught.value.status_code == 500
    assert caught.value.error == ""


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


@pytest.mark.parametrize(
    ("code", "reason"),
    [(1000, "normal closure"), (1001, "going away")],
)
async def test_events_end_quietly_on_a_clean_close(code: int, reason: str) -> None:
    """A server closing properly ends the iteration, it does not raise."""

    async def close_cleanly(
        connection: websockets.asyncio.server.ServerConnection,
    ) -> None:
        await connection.send(json.dumps({"event_type": "loop", "event": {}}))
        await connection.close(code, reason)

    async with serving(handler=close_cleanly) as (url, _):
        async with socket(url) as ws:
            seen = [event async for event in ws.events()]
    assert [event["event_type"] for event in seen] == ["loop"]


@pytest.mark.parametrize("code", [1006, 1011])
async def test_events_raise_on_a_connection_that_breaks(code: int) -> None:
    """An abnormal close reaches the caller instead of looking like the end."""

    async def break_off(connection: websockets.asyncio.server.ServerConnection) -> None:
        await connection.send(json.dumps({"event_type": "loop", "event": {}}))
        await asyncio.sleep(0.05)
        if code == 1006:
            connection.transport.abort()  # no close frame at all
        else:
            await connection.close(code, "boom")

    seen = []
    async with serving(handler=break_off) as (url, _):
        # A short close timeout only shortens the teardown of a socket that
        # is already gone.
        async with socket(url, close_timeout=1.0) as ws:
            with pytest.raises(WebSocketError, match="Connection lost"):
                async for event in ws.events():
                    seen.append(event)
    assert [event["event_type"] for event in seen] == ["loop"]


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


async def test_aexit_forgets_a_connection_even_when_cancelled() -> None:
    """A cancelled close must not leave a dead connection behind."""
    ws, conn = connected()
    conn.close.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await ws.__aexit__(None, None, None)
    assert ws._connection is None


async def test_send_after_the_server_disappears() -> None:
    """A real dead socket, not a mocked one, still reports as WebSocketError."""

    async def drop(connection: websockets.asyncio.server.ServerConnection) -> None:
        connection.transport.abort()

    async with serving(handler=drop) as (url, _):
        async with socket(url) as ws:
            await asyncio.sleep(0.05)
            with pytest.raises(WebSocketError, match="Failed to send"):
                for _ in range(100):
                    await ws.send_key("KeyA", state=True)


async def test_aexit_none_connection() -> None:
    ws = socket()
    assert ws._connection is None
    await ws.__aexit__(None, None, None)
    assert ws._connection is None
