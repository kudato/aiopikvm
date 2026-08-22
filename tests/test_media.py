"""kvmd-media tests: the REST endpoint and the video socket.

Everything here is driven by the `media_stream` scenario, which was recorded
by hand against a real device. It holds no frame payloads — those are a
picture of the attached host's screen — so a frame is rebuilt from its
recorded length and its recorded first bytes, which is what the framing is
made of anyway.
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
import websockets.exceptions
import websockets.http11
from websockets.datastructures import Headers

from aiopikvm import (
    APIError,
    ConfigurationError,
    MediaWebSocket,
    PiKVM,
    ResponseError,
    WebSocketError,
)
from tests.fixtures import load_json


def step(name: str) -> dict[str, Any]:
    """Return one recorded step of the live-video scenario.

    Args:
        name: Step name from the ``media_stream`` scenario.

    Returns:
        The recorded step.

    Raises:
        KeyError: If the scenario has no such step.
    """
    steps = load_json("media_stream")["steps"]
    for recorded in steps:
        if recorded["name"] == name:
            return dict(recorded)
    known = ", ".join(recorded["name"] for recorded in steps)
    raise KeyError(f"Unknown media_stream step {name!r}; recorded: {known}")


def frame_bytes(recorded: dict[str, Any]) -> bytes:
    """Rebuild a recorded binary message.

    The scenario stores each message's length and its first few bytes and
    nothing else, deliberately. Padding the head out to the recorded length
    gives back everything this client looks at: the operation byte, the
    keyframe flag, and the Annex B start code behind them.

    Args:
        recorded: One entry of a step's ``frames`` list.

    Returns:
        The message as it went over the wire, its payload replaced by zeros.
    """
    head = bytes.fromhex(recorded["data_head"])
    return head + bytes(recorded["data_len"] - len(head))


def messages(name: str) -> list[str | bytes]:
    """Return a recorded socket session as frames a mock can hand out.

    Args:
        name: Step name from the ``media_stream`` scenario.

    Returns:
        Each recorded frame, text as JSON and binary as bytes.
    """
    out: list[str | bytes] = []
    for recorded in step(name)["frames"]:
        if recorded["type"] == "text":
            out.append(json.dumps(recorded["msg"]))
        else:
            out.append(frame_bytes(recorded))
    return out


def iterating(*frames: str | bytes, closed: BaseException | None = None) -> AsyncMock:
    """Build a mock connection whose ``recv`` hands out *frames*.

    Args:
        frames: Frames to hand out, in order.
        closed: What to raise once they run out; a clean close by default,
            which is how *websockets* reports a server that said goodbye.

    Returns:
        The mock connection.
    """
    conn = AsyncMock()
    ending = closed or websockets.exceptions.ConnectionClosedOK(None, None)
    conn.recv = AsyncMock(side_effect=[*frames, ending])
    return conn


def socket(url: str = "https://pikvm.local", **kwargs: Any) -> MediaWebSocket:
    """Build a media socket against a fake host."""
    return MediaWebSocket(url, user="admin", passwd="admin", **kwargs)


def connected(*frames: str | bytes, **kwargs: Any) -> MediaWebSocket:
    """Build a media socket with a mock connection already in place."""
    ws = socket(**kwargs)
    ws._connection = iterating(*frames)
    return ws


async def opened(*frames: str | bytes, **kwargs: Any) -> MediaWebSocket:
    """Open a media socket over a mock connection handing out *frames*."""
    ws = socket(**kwargs)
    conn = iterating(*frames)
    with patch("aiopikvm._media_ws._Connector", AsyncMock(return_value=conn)):
        return await ws.__aenter__()


def sent(ws: MediaWebSocket) -> Any:
    """Return the last frame handed to the connection."""
    conn: AsyncMock = ws._connection  # type: ignore[assignment]
    return conn.send.call_args[0][0]


def rejection(name: str) -> websockets.exceptions.InvalidStatus:
    """Build the handshake refusal kvmd was recorded answering with.

    Args:
        name: Step name from the ``media_stream`` scenario.

    Returns:
        The exception *websockets* raises for that response.
    """
    recorded = step(name)
    body = json.dumps(recorded["response"]).encode()
    return websockets.exceptions.InvalidStatus(
        websockets.http11.Response(
            recorded["status"],
            "Bad Request",
            Headers(
                {
                    "Content-Type": recorded["content_type"],
                    "Content-Length": str(len(body)),
                }
            ),
            body,
        )
    )


# --- The REST endpoint ---------------------------------------------------


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/media").mock(
        return_value=httpx.Response(200, json=step("media_state")["response"])
    )
    state = await client.media.get_state()
    assert state.video.h264 is not None
    assert state.video.h264.profile_level_id == "42E01F"
    # The recording device serves H.264 only; a format it does not have is
    # simply absent rather than present and empty.
    assert state.video.jpeg is None


async def test_get_state_without_the_media_block(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/media").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    with pytest.raises(ResponseError, match="MediaState cannot parse"):
        await client.media.get_state()


async def test_media_resource_is_dropped_on_close(client: PiKVM) -> None:
    resource = client.media
    await client.aclose()
    assert "media" not in client.__dict__
    assert resource is not None


# --- The socket's URL ----------------------------------------------------


def test_pure_socket_names_its_format() -> None:
    assert socket()._url == "wss://pikvm.local/api/media/ws?video=h264"
    assert socket().pure is True


def test_regular_socket_names_none() -> None:
    assert socket(video=None)._url == "wss://pikvm.local/api/media/ws"
    assert socket(video=None).pure is False


def test_format_is_quoted() -> None:
    # Nothing the daemon serves needs it, but a format is a caller's string
    # and must not be able to add a second query parameter.
    assert socket(video="h264&x=1")._url.endswith("?video=h264%26x%3D1")


def test_url_construction_http() -> None:
    assert (
        socket("http://pikvm.local")._url == "ws://pikvm.local/api/media/ws?video=h264"
    )


def test_unsupported_url_scheme() -> None:
    with pytest.raises(ConfigurationError, match="Unsupported URL scheme"):
        socket("ftp://pikvm.local")


# --- The handshake -------------------------------------------------------


@pytest.mark.parametrize("name", ["media_ws_jpeg", "media_ws_unknown"])
async def test_a_format_the_daemon_lacks_is_refused(name: str) -> None:
    ws = socket(video=step(name)["request"]["params"]["video"])
    with patch("aiopikvm._media_ws._Connector", AsyncMock(side_effect=rejection(name))):
        with pytest.raises(APIError) as caught:
            await ws.__aenter__()
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"
    assert "Unsupported video type" in str(caught.value)


async def test_a_broken_connection_is_reported() -> None:
    ws = socket()
    with patch(
        "aiopikvm._media_ws._Connector", AsyncMock(side_effect=OSError("no route"))
    ):
        with pytest.raises(WebSocketError, match="Failed to connect"):
            await ws.__aenter__()


async def test_a_pure_socket_reads_nothing_on_open() -> None:
    ws = await opened(*messages("media_ws_pure"))
    try:
        # The daemon is already streaming; there is no announcement to wait
        # for, and waiting for one would eat the first frame.
        assert ws.media is None
        conn: AsyncMock = ws._connection  # type: ignore[assignment]
        assert conn.recv.await_count == 0
    finally:
        await ws.__aexit__(None, None, None)


async def test_a_regular_socket_reads_its_announcement_on_open() -> None:
    ws = await opened(*messages("media_ws_regular"), video=None)
    try:
        assert ws.media is not None
        assert ws.media.video.h264 is not None
        assert ws.media.video.h264.profile_level_id == "42E01F"
    finally:
        await ws.__aexit__(None, None, None)


async def test_an_announcement_that_is_not_one_fails_the_open() -> None:
    ws = socket(video=None)
    conn = iterating(json.dumps({"event_type": "pong", "event": {}}))
    with patch("aiopikvm._media_ws._Connector", AsyncMock(return_value=conn)):
        with pytest.raises(ResponseError, match="other than the daemon's"):
            await ws.__aenter__()
    # __aexit__ never runs for a failed __aenter__, so the open socket has to
    # be closed here or kvmd keeps it until the process ends.
    conn.close.assert_awaited_once()
    assert ws._connection is None


async def test_an_unreadable_announcement_fails_the_open() -> None:
    ws = socket(video=None)
    conn = iterating(json.dumps({"event_type": "media", "event": {"video": 1}}))
    with patch("aiopikvm._media_ws._Connector", AsyncMock(return_value=conn)):
        with pytest.raises(ResponseError, match="MediaState cannot"):
            await ws.__aenter__()
    conn.close.assert_awaited_once()


async def test_a_socket_that_closes_before_announcing_fails_the_open() -> None:
    ws = socket(video=None)
    conn = iterating()
    with patch("aiopikvm._media_ws._Connector", AsyncMock(return_value=conn)):
        with pytest.raises(WebSocketError, match="closed before the daemon"):
            await ws.__aenter__()


async def test_a_silent_socket_fails_the_open() -> None:
    async def never() -> str:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    ws = socket(video=None, open_timeout=0.01)
    conn = AsyncMock()
    conn.recv = never
    with patch("aiopikvm._media_ws._Connector", AsyncMock(return_value=conn)):
        with pytest.raises(WebSocketError, match="did not say what it can send"):
            await ws.__aenter__()


# --- Frames --------------------------------------------------------------


async def test_pure_frames_are_the_whole_message() -> None:
    recorded = step("media_ws_pure")["frames"]
    ws = connected(*[frame_bytes(entry) for entry in recorded])
    frames = [frame async for frame in ws.frames()]
    assert len(frames) == len(recorded)
    for frame, entry in zip(frames, recorded, strict=True):
        assert len(frame.data) == entry["data_len"]
        # Annex B: a start code, then the NAL header. 0x27 opens a keyframe,
        # 0x21 is a delta frame.
        assert frame.data[:4] == b"\x00\x00\x00\x01"
        # A pure socket carries no flag, so nothing may be claimed about it.
        assert frame.key is None
    assert frames[0].data[4] == 0x27
    assert frames[1].data[4] == 0x21


async def test_regular_frames_carry_a_keyframe_flag() -> None:
    ws = await opened(*messages("media_ws_regular"), video=None)
    frames = [frame async for frame in ws.frames()]
    # Six recorded messages: the announcement (consumed on open), two pongs,
    # and three frames.
    assert [frame.key for frame in frames] == [True, False, True]
    assert all(frame.data[:4] == b"\x00\x00\x00\x01" for frame in frames)
    recorded = step("media_ws_regular")["frames"][2]
    # The operation byte and the flag are stripped; the video is not.
    assert len(frames[0].data) == recorded["data_len"] - 2


async def test_a_clean_close_ends_the_iteration() -> None:
    ws = connected()
    assert [frame async for frame in ws.frames()] == []


async def test_a_broken_connection_ends_it_loudly() -> None:
    ws = socket()
    ws._connection = iterating(
        closed=websockets.exceptions.ConnectionClosedError(None, None)
    )
    with pytest.raises(WebSocketError, match="Connection lost"):
        [frame async for frame in ws.frames()]


async def test_reading_without_a_connection() -> None:
    ws = socket()
    with pytest.raises(WebSocketError, match="Not connected"):
        [frame async for frame in ws.frames()]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (b"", "empty binary media frame"),
        (bytes([1]), "no keyframe flag"),
        (bytes([7, 0, 1]), "unknown op 7"),
        ("not json at all", "malformed media message"),
        (json.dumps({"event_type": "nope"}), "unexpected media message"),
    ],
)
async def test_a_frame_that_says_nothing_is_dropped(
    message: str | bytes,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = connected(message, video=None)
    with caplog.at_level("WARNING", logger="aiopikvm._media_ws"):
        assert [frame async for frame in ws.frames()] == []
    assert expected in caplog.text


async def test_the_pong_is_consumed_in_silence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = connected(bytes([255]), video=None)
    with caplog.at_level("WARNING", logger="aiopikvm._media_ws"):
        assert [frame async for frame in ws.frames()] == []
    assert caplog.text == ""


async def test_text_on_a_pure_socket_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = connected(json.dumps({"event_type": "nope"}))
    with caplog.at_level("WARNING", logger="aiopikvm._media_ws"):
        assert [frame async for frame in ws.frames()] == []
    assert "unexpected media message" in caplog.text


# --- Sending -------------------------------------------------------------


async def test_start_asks_for_a_format() -> None:
    ws = connected(video=None)
    await ws.start()
    assert json.loads(sent(ws)) == {
        "event_type": "start",
        "event": {"type": "video", "format": "h264"},
    }


async def test_start_is_refused_on_a_pure_socket() -> None:
    ws = connected()
    with pytest.raises(ConfigurationError, match="starts the stream during"):
        await ws.start()


async def test_a_keyframe_can_be_asked_for_on_either_socket() -> None:
    for ws in (connected(), connected(video=None)):
        await ws.request_keyframe()
        assert sent(ws) == bytes([1])


async def test_ping_is_refused_on_a_pure_socket() -> None:
    ws = connected()
    with pytest.raises(ConfigurationError, match="does not answer pings"):
        await ws.ping()


async def test_ping_goes_out_on_a_regular_socket() -> None:
    ws = connected(video=None)
    await ws.ping()
    assert sent(ws) == bytes([0])


async def test_sending_without_a_connection() -> None:
    ws = socket()
    with pytest.raises(WebSocketError, match="Not connected"):
        await ws.request_keyframe()


async def test_a_send_that_breaks_is_reported() -> None:
    ws = connected()
    conn: AsyncMock = ws._connection  # type: ignore[assignment]
    conn.send = AsyncMock(
        side_effect=websockets.exceptions.ConnectionClosedError(None, None)
    )
    with pytest.raises(WebSocketError, match="Failed to send keyframe request"):
        await ws.request_keyframe()


# --- The client's side ---------------------------------------------------


async def test_media_ws_inherits_the_client(client: PiKVM) -> None:
    ws = client.media_ws()
    assert ws._url == "wss://pikvm.local/api/media/ws?video=h264"
    assert ws._user == "admin"
    assert ws._passwd() == "admin"
    assert ws._open_timeout == client._timeout


async def test_media_ws_forwards_its_socket_options(client: PiKVM) -> None:
    ws = client.media_ws(video=None, max_size=1024, max_queue=4, ping_interval=None)
    assert ws._max_size == 1024
    assert ws._max_queue == 4
    assert ws._ping_interval is None


async def test_media_ws_defaults_to_a_deeper_queue(client: PiKVM) -> None:
    # websockets' own default is 16 frames, which is under a second of video:
    # once the buffer fills it stops reading the socket, and its keepalive
    # then times out on a connection whose only problem was a slow consumer.
    assert client.media_ws()._max_queue > 16
    assert client.media_ws()._max_size is None


async def test_media_ws_after_close(client: PiKVM) -> None:
    await client.aclose()
    with pytest.raises(ConfigurationError, match="has been closed"):
        client.media_ws()


async def test_media_ws_with_cookie_auth_and_no_session() -> None:
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="x", auth="cookie"
    ) as kvm:
        with pytest.raises(ConfigurationError, match="media_ws\\(\\) cannot log in"):
            kvm.media_ws()
