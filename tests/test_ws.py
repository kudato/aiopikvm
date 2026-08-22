"""PiKVMWebSocket tests."""

import asyncio
import json
import logging
import ssl
import struct
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
    ATXState,
    AuthError,
    BusyError,
    ConfigurationError,
    GPIOState,
    HIDKeymaps,
    HIDState,
    KvmdVersion,
    MSDState,
    OCRInfo,
    PiKVMWebSocket,
    RedirectError,
    ResponseError,
    StreamerState,
    SwitchState,
    UnavailableError,
    WebSocketError,
)
from aiopikvm._ws import _PENDING_LIMIT, _Connector, _merge
from tests.fixtures import load_json, load_jsonl


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
    """Return the last frame handed to the connection, parsed as JSON."""
    frame: dict[str, Any] = json.loads(conn.send.call_args[0][0])
    return frame


def sent_bytes(conn: AsyncMock) -> bytes:
    """Return the last frame handed to the connection, as it went out."""
    frame: bytes = conn.send.call_args[0][0]
    return frame


def recorded(event_type: str) -> dict[str, Any]:
    """Return the first recorded event of that type.

    Args:
        event_type: kvmd event name, such as ``"loop"`` or ``"pong"``.

    Returns:
        The event as kvmd sent it, envelope included.

    Raises:
        KeyError: If the recorded session contains no such event.
    """
    for line in load_jsonl("ws_events"):
        if line["msg"]["event_type"] == event_type:
            event: dict[str, Any] = line["msg"]
            return event
    raise KeyError(f"No {event_type!r} event in the recorded session")


def iterating(*messages: str | bytes, closed: BaseException | None = None) -> AsyncMock:
    """Build a mock connection whose ``recv`` hands out *messages*.

    Args:
        messages: Frames to hand out, in order.
        closed: What to raise once they run out; a clean close by default,
            which is how *websockets* reports a server that said goodbye.

    Returns:
        The mock connection.
    """
    conn = AsyncMock()
    ending = closed or websockets.exceptions.ConnectionClosedOK(None, None)
    conn.recv = AsyncMock(side_effect=[*messages, ending])
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


async def answering(connection: websockets.asyncio.server.ServerConnection) -> None:
    """Answer pings the way kvmd does, on the channel they arrived on.

    kvmd sends the ``loop`` event first, replies to the ``ping`` event with a
    ``pong`` event, and replies to binary op 0 with binary op 255. Everything
    it is sent here is a frame this client built.

    Args:
        connection: The accepted connection.
    """
    await connection.send(json.dumps(recorded("loop")))
    async for message in connection:
        if isinstance(message, bytes):
            if message[:1] == bytes([0]):
                await connection.send(bytes([255]))
        elif json.loads(message)["event_type"] == "ping":
            await connection.send(json.dumps(recorded("pong")))


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
        ctx = mock_connect.call_args[1]["ssl_context"]
        assert ctx.verify_mode is ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
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
        proxy=None,
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
    ws._connection = iterating(json.dumps(recorded("loop")))
    assert [event async for event in ws.events()] == [recorded("loop")]


async def test_events_skips_malformed_json(caplog: pytest.LogCaptureFixture) -> None:
    ws = socket()
    ws._connection = iterating("not valid json", json.dumps(recorded("atx")))

    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        events = [event async for event in ws.events()]

    assert events == [recorded("atx")]
    assert "Skipping malformed WebSocket message" in caplog.text


async def test_events_skip_json_that_is_not_an_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """kvmd's own parser refuses these too; the annotation says dict."""
    ws = socket()
    ws._connection = iterating("[1, 2]", json.dumps(recorded("atx")))

    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        events = [event async for event in ws.events()]

    assert events == [recorded("atx")]
    assert "not an event object: list" in caplog.text


async def test_events_yield_the_json_pong() -> None:
    """kvmd sends it as an ordinary event, so it stays one (#81)."""
    ws = socket()
    ws._connection = iterating(json.dumps(recorded("pong")))
    assert [event async for event in ws.events()] == [recorded("pong")]


async def test_events_drop_the_binary_pong() -> None:
    """The answer to a binary ping is an op, not an event (#81)."""
    ws = socket()
    ws._connection = iterating(bytes([255]), json.dumps(recorded("atx")))
    assert [event async for event in ws.events()] == [recorded("atx")]


async def test_events_drop_an_unknown_binary_op(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A binary frame is an operation and a payload, never JSON (#81)."""
    ws = socket()
    ws._connection = iterating(
        json.dumps(recorded("atx")).encode(), json.dumps(recorded("atx"))
    )

    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        events = [event async for event in ws.events()]

    assert events == [recorded("atx")]
    assert "unknown op 123" in caplog.text


async def test_events_drop_an_empty_binary_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = socket()
    ws._connection = iterating(b"", json.dumps(recorded("atx")))

    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        events = [event async for event in ws.events()]

    assert events == [recorded("atx")]
    assert "empty binary WebSocket frame" in caplog.text


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


async def test_events_report_a_read_that_fails_for_another_reason() -> None:
    """Whatever websockets raises, the caller sees a PiKVMError."""
    ws = socket()
    ws._connection = iterating(closed=websockets.exceptions.ProtocolError("bad frame"))
    with pytest.raises(WebSocketError, match="Failed to read from the socket"):
        await anext(ws.events())


async def test_events_require_a_connection() -> None:
    ws = socket()
    with pytest.raises(WebSocketError, match="Not connected"):
        await anext(ws.events())


# --- Typed states --------------------------------------------------------


def replaying() -> AsyncMock:
    """Build a connection that replays the recorded kvmd session."""
    return iterating(*(json.dumps(line["msg"]) for line in load_jsonl("ws_events")))


async def test_states_type_every_subsystem_kvmd_sent() -> None:
    """The recorded session ends with each subsystem parsed by its model (#61)."""
    ws = socket()
    ws._connection = replaying()
    snapshots = [state async for state in ws.states()]

    final = snapshots[-1]
    assert isinstance(final.atx, ATXState)
    assert isinstance(final.gpio, GPIOState)
    assert isinstance(final.hid, HIDState)
    assert isinstance(final.hid_keymaps, HIDKeymaps)
    assert isinstance(final.msd, MSDState)
    assert isinstance(final.ocr, OCRInfo)
    assert isinstance(final.streamer, StreamerState)
    assert isinstance(final.switch, SwitchState)
    assert final.clients == 1


async def test_states_skip_the_events_that_are_not_state() -> None:
    """loop and pong say nothing about the device, so nothing comes out."""
    ws = socket()
    ws._connection = replaying()
    updated = [state.updated async for state in ws.states()]
    assert "loop" not in updated
    assert "pong" not in updated
    assert set(updated) == {
        "atx",
        "clients",
        "gpio",
        "hid",
        "hid_keymaps",
        "info",
        "msd",
        "ocr",
        "streamer",
        "switch",
    }


async def test_states_merge_a_partial_update() -> None:
    """kvmd's later streamer events carry one key; the rest must survive (#61).

    The recorded session has exactly that: a full streamer state, then two
    events with nothing in them but ``streamer``.
    """
    ws = socket()
    ws._connection = replaying()
    streamer = [
        state.streamer async for state in ws.states() if state.updated == "streamer"
    ]
    assert len(streamer) >= 2, "the capture must contain a partial update"
    assert all(state is not None for state in streamer)
    first, last = streamer[0], streamer[-1]
    assert last is not None and first is not None
    assert last.features == first.features, "a field nobody updated stays put"
    assert last.streamer is not None


async def test_states_merge_the_info_subsystems() -> None:
    """kvmd sends one /api/info key per event, never a bundle (#61)."""
    ws = socket()
    ws._connection = replaying()
    info = [state.info async for state in ws.states() if state.updated == "info"]
    last = info[-1]
    assert last is not None
    # Every submanager arrived in its own event and none overwrote another.
    assert last.auth is not None
    assert last.fan is not None
    assert last.node is not None
    assert last.meta is not None
    assert last.uptime is not None
    assert last.health is not None
    assert last.system is not None
    assert last.extras is not None


async def test_states_type_the_info_subsystems() -> None:
    """The merged /api/info is a model, not a dictionary (#71)."""
    ws = socket()
    ws._connection = replaying()
    info = [state.info async for state in ws.states() if state.updated == "info"]
    last = info[-1]
    assert last is not None
    assert last.system is not None
    assert last.system.kvmd.version == "4.206"
    assert last.health is not None
    assert isinstance(last.health.temp.cpu, float)
    assert last.auth is not None
    assert last.auth.enabled is True


async def test_states_do_not_change_a_snapshot_already_handed_out() -> None:
    """A caller keeping an old snapshot must keep what it said."""
    ws = socket()
    ws._connection = replaying()
    snapshots = [state async for state in ws.states()]
    uptimes = [
        state.info.uptime.total
        for state in snapshots
        if state.updated == "info"
        and state.info is not None
        and state.info.uptime is not None
    ]
    assert len(set(uptimes)) > 1, "the capture must contain two different uptimes"


async def test_states_report_a_payload_no_model_can_parse() -> None:
    """A pydantic failure would land outside the exception hierarchy."""
    ws = socket()
    ws._connection = iterating(
        json.dumps({"event_type": "atx", "event": {"leds": "not an object"}})
    )
    with pytest.raises(ResponseError, match="atx WebSocket event"):
        await anext(ws.states())


async def test_states_ignore_an_event_type_they_do_not_know() -> None:
    """A newer kvmd broadcasting something new must not stop the iteration."""
    ws = socket()
    ws._connection = iterating(
        json.dumps({"event_type": "quantum", "event": {"spin": "up"}}),
        json.dumps(recorded("atx")),
    )
    assert [state.updated async for state in ws.states()] == ["atx"]


@pytest.mark.parametrize(
    "event",
    [
        {"event_type": "atx"},
        {"event_type": "atx", "event": None},
        {"event": {"count": 1}},
        {"event_type": "clients", "event": {"count": "many"}},
    ],
)
async def test_states_ignore_a_frame_that_is_not_shaped_like_an_event(
    event: dict[str, Any],
) -> None:
    ws = socket()
    ws._connection = iterating(json.dumps(event), json.dumps(recorded("msd")))
    assert [state.updated async for state in ws.states()] == ["msd"]


async def test_states_end_with_the_connection() -> None:
    """Whatever events() does about the connection, states() does too."""
    ws = socket()
    ws._connection = iterating(
        json.dumps(recorded("atx")),
        closed=websockets.exceptions.ConnectionClosedError(None, None),
    )
    seen = []
    with pytest.raises(WebSocketError, match="Connection lost"):
        async for state in ws.states():
            seen.append(state)
    assert [state.updated for state in seen] == ["atx"]


def test_merge_keeps_what_the_update_does_not_mention() -> None:
    """The merge is the piece the partial updates hang on."""
    base = {"keyboard": {"online": True, "leds": {"caps": False}}, "busy": False}
    merged = _merge(base, {"keyboard": {"leds": {"caps": True}}})
    assert merged == {
        "keyboard": {"online": True, "leds": {"caps": True}},
        "busy": False,
    }
    assert base["keyboard"] == {"online": True, "leds": {"caps": False}}, "not in place"


def test_merge_replaces_rather_than_merges_a_value_that_is_not_an_object() -> None:
    """kvmd nulls a whole subtree — a stopped streamer, an ejected image."""
    assert _merge({"streamer": {"pid": 1}}, {"streamer": None}) == {"streamer": None}
    assert _merge({"streamer": None}, {"streamer": {"pid": 2}}) == {
        "streamer": {"pid": 2}
    }


# --- Sending -------------------------------------------------------------


async def test_not_connected_error() -> None:
    with pytest.raises(WebSocketError, match="Not connected"):
        await socket().send_key("KeyA", state=True)


async def test_send_key() -> None:
    ws, conn = connected()
    await ws.send_key("KeyA", state=True)
    assert sent(conn) == {"event_type": "key", "event": {"key": "KeyA", "state": True}}


async def test_send_key_can_ask_kvmd_to_release_it() -> None:
    """A press and its release asked for in one event (#74)."""
    ws, conn = connected()
    await ws.send_key("KeyA", state=True, finish=True)
    assert sent(conn) == {
        "event_type": "key",
        "event": {"key": "KeyA", "state": True, "finish": True},
    }


async def test_send_key_leaves_finish_out_of_a_release() -> None:
    """kvmd parses the flag on a release and acts on it only on a press (#74).

    The event that goes out is the one a client that never heard of the flag
    would send.
    """
    ws, conn = connected()
    await ws.send_key("KeyA", state=False, finish=True)
    assert sent(conn) == {"event_type": "key", "event": {"key": "KeyA", "state": False}}


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


async def test_send_mouse_relative() -> None:
    """kvmd takes a single step as an object, the way its web UI sends it."""
    ws, conn = connected()
    await ws.send_mouse_relative(3, -4)
    assert sent(conn) == {
        "event_type": "mouse_relative",
        "event": {"delta": {"x": 3, "y": -4}},
    }


@pytest.mark.parametrize(
    ("method", "event_type"),
    [
        ("send_mouse_relative_batch", "mouse_relative"),
        ("send_mouse_wheel_batch", "mouse_wheel"),
    ],
)
@pytest.mark.parametrize("squash", [False, True])
async def test_send_delta_batch(method: str, event_type: str, squash: bool) -> None:
    """A batch is a list of deltas plus the flag kvmd reads beside it (#60)."""
    ws, conn = connected()
    await getattr(ws, method)([(1, 2), (3, 4)], squash=squash)
    assert sent(conn) == {
        "event_type": event_type,
        "event": {
            "delta": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
            "squash": squash,
        },
    }


@pytest.mark.parametrize(
    "method", ["send_mouse_relative_batch", "send_mouse_wheel_batch"]
)
async def test_send_delta_batch_takes_any_iterable(method: str) -> None:
    """A caller draining a queue should not have to build a list first."""
    ws, conn = connected()
    await getattr(ws, method)((step, -step) for step in (1, 2))
    assert sent(conn)["event"]["delta"] == [{"x": 1, "y": -1}, {"x": 2, "y": -2}]


@pytest.mark.parametrize(
    "method", ["send_mouse_relative_batch", "send_mouse_wheel_batch"]
)
async def test_send_an_empty_delta_batch(method: str) -> None:
    """kvmd builds an empty delta list and does nothing with it."""
    ws, conn = connected()
    await getattr(ws, method)([])
    assert sent(conn)["event"] == {"delta": [], "squash": False}


async def test_send_on_a_broken_connection() -> None:
    """A dead socket must not leak the websockets exception to the caller."""
    ws, conn = connected()
    conn.send.side_effect = websockets.exceptions.ConnectionClosedError(None, None)
    with pytest.raises(WebSocketError, match="Failed to send 'key'"):
        await ws.send_key("KeyA", state=True)


# --- Sending over the binary channel -------------------------------------
#
# A frame checked against `frame(...)` was sent to a real device and accepted
# by it: the ws_binary scenario records each one with kvmd's inactivity
# counter read before and after, and kvmd only bumps that counter for a frame
# it decoded. A test that spells the bytes out instead is checking this file's
# idea of the layout against itself, which is worth knowing when one fails.


def binary_step(name: str) -> dict[str, Any]:
    """Return one step of the recorded binary session.

    Args:
        name: Step name from the ``ws_binary`` scenario.

    Returns:
        The recorded step.

    Raises:
        KeyError: If the scenario has no such step.
    """
    for recorded_step in load_json("ws_binary")["steps"]:
        if recorded_step["name"] == name:
            found: dict[str, Any] = recorded_step
            return found
    raise KeyError(f"No {name!r} step in the recorded binary session")


def frame(name: str) -> bytes:
    """Return the frame the device accepted under that name.

    Args:
        name: Step name from the ``ws_binary`` scenario.

    Returns:
        The frame as it went out.
    """
    recorded_step = binary_step(name)
    assert recorded_step["accepted"] is recorded_step["expected_accepted"], (
        f"the device disagreed about {name!r} when this was recorded"
    )
    return bytes.fromhex(recorded_step["frame"])


async def test_binary_mouse_button() -> None:
    """The same layout as a key, with a button name in place of the key."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_button("middle", False)
    assert sent_bytes(conn) == frame("mouse_button")


async def test_binary_mouse_button_press() -> None:
    """Only the recorded release went to the device; the bit is the same one."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_button("middle", True)
    assert sent_bytes(conn) == bytes([2, 0b01]) + b"middle"


async def test_binary_mouse_move() -> None:
    """kvmd unpacks the position as two big-endian signed shorts."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_move(0, 0)
    assert sent_bytes(conn) == frame("mouse_move")
    assert sent_bytes(conn) == bytes([3]) + struct.pack(">hh", 0, 0)


async def test_binary_mouse_move_corner() -> None:
    ws, conn = connected(binary=True)
    await ws.send_mouse_move(-32768, 32767)
    assert sent_bytes(conn) == bytes([3]) + struct.pack(">hh", -32768, 32767)


async def test_binary_mouse_move_clamps() -> None:
    """A JSON event is clamped by kvmd; a binary one has no room for it."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_move(99999, -99999)
    assert sent_bytes(conn) == bytes([3]) + struct.pack(">hh", 32767, -32768)


async def test_binary_mouse_wheel() -> None:
    """The byte after the op is kvmd's squash flag, then one signed pair."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_wheel(0, 0)
    assert sent_bytes(conn) == frame("mouse_wheel")


async def test_binary_mouse_wheel_step() -> None:
    ws, conn = connected(binary=True)
    await ws.send_mouse_wheel(0, -5)
    assert sent_bytes(conn) == bytes([5, 0]) + struct.pack(">bb", 0, -5)


async def test_binary_mouse_wheel_clamps() -> None:
    ws, conn = connected(binary=True)
    await ws.send_mouse_wheel(1000, -1000)
    assert sent_bytes(conn) == bytes([5, 0]) + struct.pack(">bb", 127, -127)


async def test_binary_mouse_relative() -> None:
    """op 4 has the same shape as the wheel: a squash flag, then pairs (#60)."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_relative(3, -4)
    assert sent_bytes(conn) == frame("mouse_relative")
    assert sent_bytes(conn) == bytes([4, 0]) + struct.pack(">bb", 3, -4)


@pytest.mark.parametrize(
    ("method", "op"),
    [("send_mouse_relative_batch", 4), ("send_mouse_wheel_batch", 5)],
)
@pytest.mark.parametrize(("squash", "flag"), [(False, 0b00), (True, 0b01)])
async def test_binary_delta_batch(
    method: str, op: int, squash: bool, flag: int
) -> None:
    """kvmd reads the flag out of the first byte, then unpacks pairs (#60)."""
    ws, conn = connected(binary=True)
    await getattr(ws, method)([(1, 2), (-3, -4)], squash=squash)
    assert sent_bytes(conn) == bytes([op, flag]) + struct.pack(">bbbb", 1, 2, -3, -4)


async def test_binary_relative_batch_matches_the_recorded_frame() -> None:
    """The device took this one, pairs and all (#60)."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_relative_batch([(1, 2), (-3, -4)])
    assert sent_bytes(conn) == frame("mouse_relative_batch")


async def test_binary_squashed_wheel_batch_matches_the_recorded_frame() -> None:
    """kvmd added these two steps into one report when it was recorded (#60)."""
    ws, conn = connected(binary=True)
    await ws.send_mouse_wheel_batch([(0, -1), (0, -1)], squash=True)
    assert sent_bytes(conn) == frame("mouse_wheel_batch_squashed")


@pytest.mark.parametrize(
    "method", ["send_mouse_relative_batch", "send_mouse_wheel_batch"]
)
async def test_binary_delta_batch_clamps_every_step(method: str) -> None:
    ws, conn = connected(binary=True)
    await getattr(ws, method)([(1000, 0), (0, -1000)])
    assert sent_bytes(conn)[2:] == struct.pack(">bbbb", 127, 0, 0, -127)


@pytest.mark.parametrize(
    ("method", "op"),
    [("send_mouse_relative_batch", 4), ("send_mouse_wheel_batch", 5)],
)
async def test_binary_empty_delta_batch(method: str, op: int) -> None:
    """The frame is the op and the flag; kvmd's unpack loop runs zero times."""
    ws, conn = connected(binary=True)
    await getattr(ws, method)([])
    assert sent_bytes(conn) == bytes([op, 0])


async def test_binary_ping_frame() -> None:
    """op 0 is the whole frame, and the device answered it with op 255."""
    ws = socket(binary=True)
    conn = iterating()
    ws._connection = conn
    with pytest.raises(WebSocketError, match="closed before kvmd answered"):
        await ws.ping(timeout=1)
    assert sent_bytes(conn) == bytes.fromhex(binary_step("ping_binary")["sent"])
    assert binary_step("pong_binary")["received"] == "ff"


@pytest.mark.parametrize("key", ["", "KeyÄ", "K" * 33])
async def test_binary_key_names_kvmd_could_not_read(key: str) -> None:
    """kvmd decodes ASCII out of 32 bytes and drops what it cannot map."""
    ws, conn = connected(binary=True)
    with pytest.raises(ConfigurationError, match="Key name"):
        await ws.send_key(key, state=True)
    conn.send.assert_not_called()


async def test_binary_button_names_kvmd_could_not_read() -> None:
    ws, conn = connected(binary=True)
    with pytest.raises(ConfigurationError, match="Mouse button name"):
        await ws.send_mouse_button("leftÄ", True)
    conn.send.assert_not_called()


async def test_binary_key_name_of_the_full_length_is_sent() -> None:
    """32 bytes is what kvmd reads, so 32 bytes has to go through."""
    ws, conn = connected(binary=True)
    await ws.send_key("K" * 32, state=True)
    assert sent_bytes(conn) == bytes([1, 1]) + b"K" * 32


@pytest.mark.parametrize(
    ("state", "finish", "flags", "recorded"),
    [
        pytest.param(False, False, 0b00, "key_release", id="release"),
        pytest.param(True, False, 0b01, "key_press", id="press"),
        pytest.param(False, True, 0b00, "key_release", id="release_asking_finish"),
        pytest.param(True, True, 0b11, "key_press_finish", id="press_with_finish"),
    ],
)
async def test_binary_key_carries_finish_in_bit_1_of_a_press(
    state: bool, finish: bool, flags: int, recorded: str
) -> None:
    """kvmd reads the state out of bit 0 and *finish* out of bit 1 (#74).

    Bit 1 rides a press and nothing else. kvmd reads it on a release too and
    does nothing with it there, so a release that asks for *finish* is the
    plain release frame, byte for byte.

    Bit 1 asks for a release rather than being one, and asking is all it is:
    ``ControlLeft`` is among the keys kvmd exempts, so the row that presses
    it and asks would be answered with a press and nothing else. What is
    under test here is the byte, not what kvmd then does with it.

    Every row is checked against a frame a real device accepted rather than
    against this test's own idea of the layout.
    """
    ws, conn = connected(binary=True)
    await ws.send_key("ControlLeft", state=state, finish=finish)
    assert sent_bytes(conn) == bytes([1, flags]) + b"ControlLeft"
    assert sent_bytes(conn) == frame(recorded)


async def test_binary_key_asks_finish_the_same_way_for_a_key_kvmd_releases() -> None:
    """Bit 1 is not what the exemption turns on (#74).

    ``ControlLeft`` carries the flag as an exempt key, where kvmd takes the
    frame and then declines to release. ``KeyA`` is an ordinary key it does
    release, and the device accepted the identical layout for it — so the
    byte says the same thing either way, and the exemption lives past it.
    """
    ws, conn = connected(binary=True)
    await ws.send_key("KeyA", state=True, finish=True)
    assert sent_bytes(conn) == frame("key_press_finish_ordinary")


async def test_json_stays_the_default() -> None:
    """Nothing changes for a client that did not ask for the binary channel."""
    ws, conn = connected()
    await ws.send_key("KeyA", state=True)
    assert sent(conn) == {"event_type": "key", "event": {"key": "KeyA", "state": True}}


# --- Ping ----------------------------------------------------------------


async def test_ping_measures_the_round_trip() -> None:
    """The JSON ping is answered by the pong event kvmd sends back (#82)."""
    async with serving(handler=answering) as (url, _):
        async with socket(url) as ws:
            latency = await ws.ping()
    assert 0 <= latency < 1


async def test_ping_over_the_binary_channel() -> None:
    """op 0 out, op 255 back — the exchange kvmd's web UI runs every second."""
    async with serving(handler=answering) as (url, _):
        async with socket(url, binary=True) as ws:
            latency = await ws.ping()
    assert 0 <= latency < 1


async def test_ping_keeps_the_events_it_reads() -> None:
    """Waiting for the pong must not swallow the events that arrive first."""
    async with serving(handler=answering) as (url, _):
        async with socket(url, binary=True) as ws:
            await ws.ping()
            seen = []
            async for event in ws.events():
                seen.append(event)
                break
    assert seen == [recorded("loop")]


async def test_ping_while_events_are_being_read() -> None:
    """The iterating task hands the pong over instead of racing for it (#82)."""
    async with serving(handler=answering) as (url, _):
        async with socket(url) as ws:
            seen: list[dict[str, Any]] = []
            reader = asyncio.create_task(_collect(ws, seen))
            await asyncio.sleep(0.05)
            latency = await ws.ping()
            await asyncio.sleep(0.05)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
    assert 0 <= latency < 1
    assert [event["event_type"] for event in seen] == ["loop", "pong"]


async def test_ping_gives_up_on_a_server_that_never_answers() -> None:
    async with serving() as (url, _):
        async with socket(url) as ws:
            with pytest.raises(WebSocketError, match="did not answer the ping"):
                await ws.ping(timeout=0.1)


async def test_ping_on_a_connection_that_closes() -> None:
    """The close ends the wait; it is not something to sit out the timeout on."""

    async def close_after_the_ping(
        connection: websockets.asyncio.server.ServerConnection,
    ) -> None:
        await connection.recv()
        await connection.close()

    async with serving(handler=close_after_the_ping) as (url, _):
        async with socket(url) as ws:
            with pytest.raises(WebSocketError, match="closed before kvmd answered"):
                await ws.ping(timeout=5)


async def test_ping_on_a_connection_that_breaks() -> None:
    async def drop_after_the_ping(
        connection: websockets.asyncio.server.ServerConnection,
    ) -> None:
        await connection.recv()
        connection.transport.abort()

    async with serving(handler=drop_after_the_ping) as (url, _):
        async with socket(url, close_timeout=1.0) as ws:
            with pytest.raises(WebSocketError, match="Connection lost"):
                await ws.ping(timeout=5)


async def test_ping_requires_a_connection() -> None:
    with pytest.raises(WebSocketError, match="Not connected"):
        await socket().ping()


async def test_ping_does_not_outlive_the_socket() -> None:
    """Closing the socket ends a ping in flight instead of leaving it hanging."""
    async with serving() as (url, _):
        ws = socket(url)
        await ws.__aenter__()
        pinging = asyncio.create_task(_ping(ws))
        await asyncio.sleep(0.05)
        await ws.__aexit__(None, None, None)
        with pytest.raises(WebSocketError, match="closed before kvmd answered"):
            await pinging


async def test_ping_answered_while_it_waits_for_its_turn_reads_nothing() -> None:
    """The pong can land between the check and the socket becoming free.

    Two tasks reading and one pong is enough for it: the ping has to notice
    that its answer already arrived, or it blocks on a frame nobody wants.
    """

    class _ContendedLock(asyncio.Lock):
        """A lock whose acquire yields, the way a contended one does."""

        async def acquire(self) -> bool:
            await asyncio.sleep(0)
            return await super().acquire()

    ws = socket()
    conn = iterating()
    ws._connection = conn
    ws._read_lock = _ContendedLock()
    waiter: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    ws._pong_waiters.append(waiter)

    waiting = asyncio.create_task(ws._wait_pong(waiter))
    await asyncio.sleep(0)
    ws._resolve_pongs()
    await waiting

    conn.recv.assert_not_called()
    assert not ws._read_lock.locked()


async def test_ping_buffers_only_so_much(caplog: pytest.LogCaptureFixture) -> None:
    """A caller that only ever pings must not accumulate a day of broadcasts."""
    ws = socket()
    with caplog.at_level(logging.WARNING, logger="aiopikvm._ws"):
        for index in range(_PENDING_LIMIT + 2):
            ws._buffer({"event_type": "info", "event": {"index": index}})
    assert len(ws._pending) == _PENDING_LIMIT
    assert ws._pending[0]["event"]["index"] == 2, "the oldest went first"
    assert caplog.text.count("Dropping WebSocket events") == 1, "warned once"


async def _collect(ws: PiKVMWebSocket, into: list[dict[str, Any]]) -> None:
    """Read events into a list until cancelled."""
    async for event in ws.events():
        into.append(event)


async def _ping(ws: PiKVMWebSocket) -> float:
    """Ping with a timeout long enough that only a failure ends it."""
    return await ws.ping(timeout=30)


# --- The kvmd version ----------------------------------------------------


async def test_version_is_unknown_before_anything_is_read() -> None:
    assert socket().version is None


async def test_version_comes_from_the_loop_event() -> None:
    """kvmd sends it first, and it is the only version the socket carries."""
    ws = socket()
    ws._connection = iterating(json.dumps(recorded("loop")))
    async for _ in ws.events():
        break
    assert ws.version == KvmdVersion(4, 206)
    assert ws.version >= (4, 100), "a version is for comparing"


async def test_version_is_read_by_a_ping_too() -> None:
    """The loop event arrives before the pong, whoever is reading."""
    async with serving(handler=answering) as (url, _):
        async with socket(url) as ws:
            await ws.ping()
            assert ws.version == (4, 206)


@pytest.mark.parametrize(
    "event",
    [None, {}, {"version": None}, {"version": {"major": 4}}, {"version": {}}],
)
async def test_version_ignores_a_loop_event_without_one(event: Any) -> None:
    """An older or newer kvmd must not break the connection over this."""
    ws = socket()
    ws._connection = iterating(json.dumps({"event_type": "loop", "event": event}))
    assert [seen async for seen in ws.events()] == [
        {"event_type": "loop", "event": event}
    ]
    assert ws.version is None


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
