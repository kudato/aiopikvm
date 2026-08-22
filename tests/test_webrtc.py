"""WebRTCSession tests, driven by a recorded Janus session.

Every message the fake gateway here sends is one the device sent — the
`janus_session` scenario, recorded by `tests/fixtures/record_janus.py`. Only
the transaction is rewritten, since the client picks its own, and nothing else
is invented: the ids, the SDP, the plugin's pushes and its refusals are all
verbatim.

The peer connection is real. aiortc parses the recorded offer, answers it and
gathers its host candidates against a server that has no media to send, which
is exactly as far as the negotiation goes before Janus says `webrtcup` — so
the whole `__aenter__` runs here without a single mock inside it.
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
import websockets.asyncio.server
import websockets.exceptions
import websockets.http11
from websockets.datastructures import Headers

from aiopikvm import (
    APIError,
    AuthError,
    ConfigurationError,
    PiKVM,
    ResponseError,
    WebRTCError,
    WebRTCEvent,
    WebRTCPluginEvent,
    WebRTCSession,
    WebSocketError,
)
from aiopikvm._webrtc import _PLUGIN, _SUBPROTOCOL, _peer_connection
from tests.fixtures import load_json
from tests.helpers import undeclared_fields

aiortc = pytest.importorskip("aiortc", reason="the webrtc extra is not installed")


def session(url: str = "https://pikvm.local", **kwargs: Any) -> WebRTCSession:
    """Build a session against a fake host.

    Args:
        url: Base URL to build it with.
        **kwargs: Anything else the session takes.

    Returns:
        The session, unopened.
    """
    return WebRTCSession(url, user="admin", passwd="admin", **kwargs)


def step(name: str) -> dict[str, Any]:
    """Return one step of the recorded Janus session.

    Args:
        name: Step name from the ``janus_session`` scenario.

    Returns:
        The recorded step.

    Raises:
        KeyError: If the scenario has no such step.
    """
    for recorded in load_json("janus_session")["steps"]:
        if recorded["name"] == name:
            return dict(recorded)
    raise KeyError(f"No {name!r} step in the recorded Janus session")


def answer_of(name: str) -> dict[str, Any]:
    """Return what Janus answered a recorded step with."""
    return dict(step(name)["response"])


def push_of(name: str) -> dict[str, Any]:
    """Return what the plugin pushed after a recorded step."""
    return dict(step(name)["push"])


SESSION_ID: int = answer_of("create")["data"]["id"]
HANDLE_ID: int = answer_of("attach")["data"]["id"]
WEBRTCUP: dict[str, Any] = step("without_a_viewer")["events"][0]
OFFER: str = push_of("watch")["jsep"]["sdp"]


def replies(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Answer one client message the way the recorded device answered it.

    Janus acknowledges a plugin message synchronously with a bare
    ``{"ok": true}`` and the plugin pushes its own event afterwards, under a
    transaction of its own — so most of these are two messages, and only the
    first carries the transaction back.

    Args:
        message: What the client sent.

    Returns:
        The messages to send back, in order.

    Raises:
        KeyError: If the client sent something the recording does not cover.
    """
    kind = message["janus"]
    if kind == "message":
        name = {
            "features": "features",
            "watch": "watch",
            "start": "start",
            "stop": "stop",
            "key_required": "key_required",
        }[message["body"]["request"]]
    elif kind == "trickle":
        name = "trickle_completed"
    else:
        name = {
            "create": "create",
            "attach": "attach",
            "keepalive": "keepalive",
            "detach": "detach",
            "destroy": "destroy",
        }[kind]

    recorded = step(name)
    out = [{**recorded["response"], "transaction": message["transaction"]}]
    if recorded.get("push") is not None:
        out.append(dict(recorded["push"]))
    if name == "trickle_completed":
        # Janus reports the peer connection up on its own, once both halves
        # of the negotiation are in and the DTLS handshake has finished.
        out.append(dict(WEBRTCUP))
    return out


Rewrite = Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]]
Aftermath = Callable[
    [websockets.asyncio.server.ServerConnection, dict[str, Any]],
    Awaitable[None],
]


def response(
    status: int, reason: str, body: bytes = b"", **headers: str
) -> websockets.http11.Response:
    """Build the HTTP response a server rejects the upgrade with."""
    sent = {"Content-Length": str(len(body)), **headers}
    return websockets.http11.Response(status, reason, Headers(sent), body)


def refusal(name: str) -> websockets.http11.Response:
    """Build the refusal the device answered a recorded upgrade with."""
    recorded = step(name)
    body = json.dumps(recorded["response"]).encode()
    return response(
        recorded["status"],
        "Unauthorized",
        body,
        **{"Content-Type": recorded["content_type"]},
    )


@asynccontextmanager
async def gateway(
    reject: websockets.http11.Response | None = None,
    rewrite: Rewrite | None = None,
    after: Aftermath | None = None,
) -> AsyncIterator[tuple[str, list[dict[str, Any]], list[websockets.http11.Request]]]:
    """Run a fake Janus gateway on loopback.

    Args:
        reject: Response to refuse the upgrade with; accept it when ``None``.
        rewrite: Last say over what goes back for a given message, for the
            tests that need the device to answer something else.
        after: Run once the answers to a message have gone out, holding the
            connection itself — for the tests about a link that goes away
            rather than one that answers wrongly.

    Yields:
        The gateway's URL, the list the client's messages accumulate in, and
        the list its handshake requests accumulate in.
    """
    seen: list[dict[str, Any]] = []
    requests: list[websockets.http11.Request] = []

    async def process(
        connection: websockets.asyncio.server.ServerConnection,
        request: websockets.http11.Request,
    ) -> websockets.http11.Response | None:
        requests.append(request)
        return reject

    async def handler(
        connection: websockets.asyncio.server.ServerConnection,
    ) -> None:
        async for raw in connection:
            message = json.loads(raw)
            seen.append(message)
            out = replies(message)
            if rewrite is not None:
                out = rewrite(message, out)
            for item in out:
                await connection.send(json.dumps(item))
            if after is not None:
                await after(connection, message)

    async with websockets.asyncio.server.serve(
        handler,
        "127.0.0.1",
        0,
        process_request=process,
        subprotocols=[_SUBPROTOCOL],
    ) as server:
        host, port = server.sockets[0].getsockname()[:2]
        yield (f"http://{host}:{port}", seen, requests)


def bodies(seen: list[dict[str, Any]]) -> list[str]:
    """Return the plugin requests the client sent, in order."""
    return [
        message["body"]["request"] for message in seen if message["janus"] == "message"
    ]


# --- URL and parameters --------------------------------------------------


def test_url_is_the_janus_socket() -> None:
    assert session()._url == "wss://pikvm.local/janus/ws"


def test_url_construction_http() -> None:
    assert session("http://pikvm.local")._url == "ws://pikvm.local/janus/ws"


def test_unsupported_url_scheme() -> None:
    with pytest.raises(ConfigurationError, match="Unsupported URL scheme"):
        session("ftp://pikvm.local")


def test_nothing_is_known_before_the_session_opens() -> None:
    """Every property is empty until Janus has answered something."""
    rtc = session()
    assert rtc.features is None
    assert rtc.session_id is None
    assert rtc.handle_id is None
    assert rtc.track() is None


def test_the_client_hands_its_own_settings_over() -> None:
    """`webrtc()` is a constructor call, and these are the values it passes."""
    kvm = PiKVM("https://pikvm.local", passwd="secret", verify_ssl=False, timeout=7.0)
    rtc = kvm.webrtc(audio=True, orientation=90, frame_buffer=64)
    assert rtc._url == "wss://pikvm.local/janus/ws"
    assert rtc._verify_ssl is False
    assert rtc._audio is True
    assert rtc._orientation == 90
    assert rtc._frame_buffer == 64
    assert rtc._open_timeout == 7.0
    assert rtc._close_timeout == 7.0


async def test_the_missing_extra_is_reported_before_anything_is_dialled() -> None:
    """A session on a device that is not there still names the real problem."""
    rtc = session("https://127.0.0.1:1")
    with patch.dict(sys.modules, {"aiortc": None}):
        with pytest.raises(ConfigurationError, match="aiopikvm\\[webrtc\\]"):
            async with rtc:
                pass


# --- The handshake --------------------------------------------------------


async def test_the_handshake_asks_for_the_janus_subprotocol() -> None:
    """Janus serves its transport under that name and no other."""
    async with gateway() as (url, _, requests):
        async with session(url):
            pass
    assert requests[0].headers["Sec-WebSocket-Protocol"] == _SUBPROTOCOL
    assert requests[0].path == "/janus/ws"


async def test_the_handshake_carries_the_credentials() -> None:
    """kvmd's auth chain sits in front of Janus and reads the same headers."""
    async with gateway() as (url, _, requests):
        async with WebRTCSession(url, user="operator", passwd="s3cret"):
            pass
    assert requests[0].headers["X-KVMD-User"] == "operator"
    assert requests[0].headers["X-KVMD-Passwd"] == "s3cret"


async def test_an_unauthenticated_upgrade_is_an_auth_error() -> None:
    """nginx refuses it before Janus is reached, with no kvmd envelope at all."""
    recorded = step("upgrade_unauthenticated")
    async with gateway(refusal("upgrade_unauthenticated")) as (url, _, _requests):
        with pytest.raises(AuthError) as caught:
            async with session(url):
                pass
    assert caught.value.status_code == recorded["status"] == 401
    assert caught.value.error == ""


async def test_a_gateway_that_is_not_there_is_a_plain_api_error() -> None:
    """502 is what the device answered a handshake Janus never accepted."""
    async with gateway(response(502, "Bad Gateway")) as (url, _, _requests):
        with pytest.raises(APIError) as caught:
            async with session(url):
                pass
    assert not isinstance(caught.value, AuthError)
    assert caught.value.status_code == 502


# --- The negotiation ------------------------------------------------------


async def test_the_negotiation_walks_the_recorded_sequence() -> None:
    """Create, attach, features, watch, start, trickle — then the teardown."""
    async with gateway() as (url, seen, _requests):
        async with session(url) as rtc:
            assert rtc.session_id == SESSION_ID
            assert rtc.handle_id == HANDLE_ID
    assert [message["janus"] for message in seen] == [
        "create",
        "attach",
        "message",
        "message",
        "message",
        "trickle",
        "message",
        "detach",
        "destroy",
    ]
    assert bodies(seen) == ["features", "watch", "start", "stop"]


async def test_the_handle_is_attached_to_the_ustreamer_plugin() -> None:
    async with gateway() as (url, seen, _requests):
        async with session(url):
            pass
    assert seen[1]["plugin"] == _PLUGIN
    assert seen[1]["session_id"] == SESSION_ID


async def test_every_message_carries_a_transaction_of_its_own() -> None:
    """Janus matches its acknowledgements by transaction, so they must differ."""
    async with gateway() as (url, seen, _requests):
        async with session(url):
            pass
    transactions = [message["transaction"] for message in seen]
    assert len(set(transactions)) == len(transactions)


async def test_watch_carries_the_parameters_the_plugin_reads() -> None:
    async with gateway() as (url, seen, _requests):
        async with session(url, audio=True, orientation=180):
            pass
    watch = seen[3]
    assert watch["body"] == {
        "request": "watch",
        "params": {
            "orientation": 180,
            "audio": True,
            "mic": False,
            "camera": False,
        },
    }


async def test_the_answer_goes_back_inside_start() -> None:
    """The plugin is the offerer here, so the client owes it the answer."""
    async with gateway() as (url, seen, _requests):
        async with session(url):
            pass
    start = seen[4]
    assert start["body"] == {"request": "start"}
    assert start["jsep"]["type"] == "answer"
    assert "m=video" in start["jsep"]["sdp"]
    assert "H264/90000" in start["jsep"]["sdp"]


async def test_the_only_trickle_says_the_gathering_is_done() -> None:
    """setLocalDescription blocks until every candidate is in the answer."""
    async with gateway() as (url, seen, _requests):
        async with session(url):
            pass
    trickle = next(message for message in seen if message["janus"] == "trickle")
    assert trickle["candidate"] == {"completed": True}
    assert trickle["handle_id"] == HANDLE_ID


async def test_the_features_the_device_announced_are_kept() -> None:
    recorded = push_of("features")["plugindata"]["data"]["result"]["features"]
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            assert rtc.features is not None
            assert rtc.features.audio is recorded["audio"]
            assert rtc.features.mic is recorded["mic"]
            assert rtc.features.ice.url == recorded["ice"]["url"]


async def test_the_device_suggests_an_ice_server_and_nothing_uses_it() -> None:
    """Contacting a third party is the caller's decision, not a default."""
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            assert rtc.features is not None
            assert rtc.features.ice.url
            assert rtc._ice_servers == []


def test_no_ice_servers_are_configured_by_default() -> None:
    """Empty, not None: aiortc reads None as "use my default", and its default
    is a public STUN server — which is the one thing this must not do on its
    own.
    """
    with patch("aiortc.RTCPeerConnection") as built:
        _peer_connection([])()
        _peer_connection(["stun:stun.example.org:3478"])()
    without, with_one = (call.args[0] for call in built.call_args_list)
    assert without.iceServers == []
    assert with_one.iceServers is not None
    assert with_one.iceServers[0].urls == "stun:stun.example.org:3478"


def test_aiortc_still_defaults_to_a_public_stun_server() -> None:
    """The reason the test above exists, pinned against the library itself."""
    from aiortc.rtcicetransport import RTCIceGatherer

    assert RTCIceGatherer.getDefaultIceServers()


# --- Teardown -------------------------------------------------------------


async def test_leaving_the_block_stops_the_stream_and_destroys_the_session() -> None:
    async with gateway() as (url, seen, _requests):
        async with session(url) as rtc:
            pass
    assert bodies(seen)[-1] == "stop"
    assert [message["janus"] for message in seen[-2:]] == ["detach", "destroy"]
    assert seen[-1]["session_id"] == SESSION_ID
    assert rtc.session_id is None
    assert rtc.handle_id is None


async def test_a_farewell_step_that_fails_does_not_take_the_rest_with_it() -> None:
    """`destroy` is the step that actually frees the session on the device.

    The others are courtesies Janus would sort out for itself. Giving up on
    the first refusal used to skip it, leaving a session behind for Janus's
    sixty-second silence timeout — and saying so at debug level only.
    """
    recorded = answer_of("message_to_a_dead_handle")

    def refuse_stop(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "stop":
            return [{**recorded, "transaction": message["transaction"]}]
        return out

    async with gateway(rewrite=refuse_stop) as (url, seen, _requests):
        async with session(url):
            pass
    assert [message["janus"] for message in seen[-2:]] == ["detach", "destroy"]


async def test_a_teardown_after_the_reader_died_does_not_wait_out_the_timeout() -> None:
    """A request nothing can acknowledge is refused, not waited on.

    `_drain`'s exit fails only the acknowledgements that existed when it left;
    one registered afterwards has nobody to resolve it, and the socket is
    still open so the message goes out. Every teardown step then ran to the
    full *open_timeout* — three of them, ten seconds each by default — and
    ended by reporting a timeout instead of the failure that caused it.

    Janus sending one frame that is not a JSON object is all it takes to put
    the reader in that state with the socket open.
    """
    armed = asyncio.Event()

    async with gateway(after=_confuse_on_a_keepalive(armed)) as (url, seen, _requests):
        rtc = session(url, keepalive_interval=0.01, open_timeout=30.0)
        await rtc.__aenter__()
        await _wait_for_the_reader(rtc, armed)
        with pytest.raises(WebSocketError, match="reader stopped"):
            # A whole teardown in a thirtieth of what one step used to take.
            async with asyncio.timeout(1.0):
                await rtc.__aexit__(None, None, None)
    # And it does not send what it knows it cannot hear the answer to. The
    # session is left for Janus's own timeout, which is what a teardown that
    # waited three times over ended up doing anyway.
    assert "stop" not in bodies(seen)


async def test_a_request_after_the_reader_died_reports_the_cause() -> None:
    """The failure, not a timeout naming the request that ran into it.

    `request_keyframe()` used to wait out *open_timeout* and then say Janus
    had not answered `message`, which is true and useless: Janus was never
    going to, and the reason it was not is the one thing worth reporting.
    """
    armed = asyncio.Event()

    async with gateway(after=_confuse_on_a_keepalive(armed)) as (url, _, _requests):
        async with session(url, keepalive_interval=0.01, open_timeout=30.0) as rtc:
            await _wait_for_the_reader(rtc, armed)
            with pytest.raises(WebSocketError, match="reader stopped"):
                async with asyncio.timeout(1.0):
                    await rtc.request_keyframe()


def _confuse_on_a_keepalive(armed: asyncio.Event) -> Aftermath:
    """Make a gateway send one frame that is not a JSON object.

    That stops the reader where it stands while leaving the socket open,
    which is the state the acknowledgements have nobody to resolve them in.
    As with the cut above, *armed* is how a test says when.

    Args:
        armed: Set by the test once the frame is allowed to go out.

    Returns:
        The hook that sends it.
    """

    async def confuse(
        connection: websockets.asyncio.server.ServerConnection,
        message: dict[str, Any],
    ) -> None:
        if armed.is_set() and message["janus"] == "keepalive":
            await connection.send(json.dumps([1, 2, 3]))

    return confuse


async def _wait_for_the_reader(rtc: WebRTCSession, armed: asyncio.Event) -> None:
    """Arm a gateway hook and wait for the session's reader to stop.

    Args:
        rtc: The open session.
        armed: The event its gateway hook is waiting on.
    """
    armed.set()
    assert rtc._reader is not None
    await asyncio.wait_for(asyncio.shield(rtc._reader), timeout=5.0)


async def test_a_failed_negotiation_still_destroys_what_it_created() -> None:
    """`__aexit__` never runs for a failed `__aenter__`, so `__aenter__` does it."""

    def refuse(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "watch":
            return [out[0], push_of("request_unknown")]
        return out

    async with gateway(rewrite=refuse) as (url, seen, _requests):
        with pytest.raises(WebRTCError):
            async with session(url):
                pass
    assert [message["janus"] for message in seen[-2:]] == ["detach", "destroy"]


async def test_a_negotiation_that_never_comes_up_times_out() -> None:
    """Janus answers everything and simply never says `webrtcup`."""

    def silent(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        return [item for item in out if item["janus"] != "webrtcup"]

    async with gateway(rewrite=silent) as (url, _, _requests):
        with pytest.raises(WebRTCError, match="did not bring the peer connection up"):
            async with session(url, negotiate_timeout=0.5):
                pass


def _cut_on_a_keepalive(armed: asyncio.Event) -> tuple[Rewrite, Aftermath]:
    """Make a gateway drop the socket on the next unanswered keepalive.

    The gateway and the client share this test's event loop, so *armed* is how
    a test says when: the background keepalive fires on its own schedule, and
    a cut that could land during `__aenter__` would prove something else.

    Args:
        armed: Set by the test once the cut is allowed to happen.

    Returns:
        The rewrite that withholds the answer and the hook that closes.
    """

    def unanswered(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        return [] if armed.is_set() and message["janus"] == "keepalive" else out

    async def cut(
        connection: websockets.asyncio.server.ServerConnection,
        message: dict[str, Any],
    ) -> None:
        if armed.is_set() and message["janus"] == "keepalive":
            await connection.close(1011, "the gateway went away")

    return unanswered, cut


async def test_a_link_that_died_unwatched_is_reported_when_the_block_ends() -> None:
    """`__aexit__` is the only place left to say it, so it has to.

    The teardown sends `stop`, `detach` and `destroy` down the same dead
    socket and swallows what comes back. That used to mark the failure as one
    the caller had been told about — which nothing had — and the block ended
    as if the video had simply finished.
    """
    armed = asyncio.Event()
    unanswered, cut = _cut_on_a_keepalive(armed)

    async with gateway(rewrite=unanswered, after=cut) as (url, _, _requests):
        with pytest.raises(WebSocketError, match="signalling connection broke"):
            async with session(url, keepalive_interval=0.01) as rtc:
                armed.set()
                assert rtc._reader is not None
                await asyncio.wait_for(asyncio.shield(rtc._reader), timeout=5.0)


async def test_a_break_the_caller_already_saw_is_not_raised_again() -> None:
    """The other half of the same bookkeeping, and the reason it exists.

    A caller who caught the failure and left the block deliberately does not
    need it a second time out of the `async with` they were on their way out
    of — so the exit stays quiet, and an exception escaping this block is the
    failure.
    """
    armed = asyncio.Event()
    unanswered, cut = _cut_on_a_keepalive(armed)

    async with gateway(rewrite=unanswered, after=cut) as (url, _, _requests):
        async with session(url) as rtc:
            armed.set()
            with pytest.raises(WebSocketError):
                await rtc.keepalive()


async def test_a_refusal_the_caller_tolerated_does_not_mute_a_later_break() -> None:
    """A request Janus refuses says nothing about the link it arrived on.

    The session goes on afterwards, so a break that comes later is still news
    — and marking every failure the caller sees would have hidden exactly the
    case `__aexit__` is there for.
    """
    recorded = answer_of("message_to_a_dead_handle")
    armed = asyncio.Event()
    unanswered, cut = _cut_on_a_keepalive(armed)

    def refuse_keyframes(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "key_required":
            return [{**recorded, "transaction": message["transaction"]}]
        return unanswered(message, out)

    async with gateway(rewrite=refuse_keyframes, after=cut) as (url, _, _requests):
        with pytest.raises(WebSocketError, match="signalling connection broke"):
            async with session(url, keepalive_interval=0.01) as rtc:
                with pytest.raises(WebRTCError) as caught:
                    await rtc.request_keyframe()
                assert caught.value.code == recorded["error"]["code"] == 459
                armed.set()
                assert rtc._reader is not None
                await asyncio.wait_for(asyncio.shield(rtc._reader), timeout=5.0)


async def test_a_clean_close_before_webrtcup_is_not_a_session() -> None:
    """A negotiation that ends without an answer has not succeeded.

    `_drain`'s exit sets `_up` so that a dead reader cannot hang the
    negotiation, which leaves a socket closed mid-handshake looking exactly
    like one that came up. `__aenter__` used to return a session whose
    `video()` yielded nothing and whose `events()` ended at once.
    """

    def silent(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        return [item for item in out if item["janus"] != "webrtcup"]

    async def bye(
        connection: websockets.asyncio.server.ServerConnection,
        message: dict[str, Any],
    ) -> None:
        if message["janus"] == "trickle":
            await connection.close()

    async with gateway(rewrite=silent, after=bye) as (url, _, _requests):
        with pytest.raises(WebSocketError, match="before the peer connection came up"):
            async with session(url):
                pass  # pragma: no cover - __aenter__ raises


async def test_a_cancelled_negotiation_is_not_replaced_by_the_recorded_failure() -> (
    None
):
    """A failed `__aenter__` tears down without raising over what it caught.

    Its cleanup used to tell `__aexit__` the block had ended cleanly, so the
    teardown raised the reader's recorded failure and the exception on its way
    out never got to be re-raised. For a `CancelledError` that means the
    cancellation is swallowed and replaced, which is what breaks
    `asyncio.timeout` and every TaskGroup around it.

    Here the keepalive task records the failure — Janus has forgotten the
    session — while the negotiation is still waiting for an answer to
    ``features`` that never comes.
    """
    recorded = answer_of("keepalive_after_destroy")

    def stall(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message["janus"] == "keepalive":
            return [{**recorded, "transaction": message["transaction"]}]
        if message.get("body", {}).get("request") == "features":
            return []
        return out

    async with gateway(rewrite=stall) as (url, _, _requests):
        rtc = session(url, keepalive_interval=0.01)

        async def open_it() -> None:
            async with rtc:
                pass  # pragma: no cover - the negotiation never finishes

        task = asyncio.create_task(open_it())
        async with asyncio.timeout(5.0):
            while rtc._keeper is None or not rtc._keeper.done():
                await asyncio.sleep(0.01)
        assert rtc._failure is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- What Janus refuses ---------------------------------------------------


async def test_a_janus_level_error_carries_its_code() -> None:
    """`janus: "error"` is the top-level shape, outside any plugin."""
    recorded = answer_of("attach_unknown_plugin")

    def refuse(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message["janus"] == "attach":
            return [{**recorded, "transaction": message["transaction"]}]
        return out

    async with gateway(rewrite=refuse) as (url, _, _requests):
        with pytest.raises(WebRTCError) as caught:
            async with session(url):
                pass
    assert caught.value.code == recorded["error"]["code"] == 460
    assert caught.value.reason == recorded["error"]["reason"]


@pytest.mark.parametrize(
    ("name", "code"),
    [("request_unknown", 405), ("request_missing", 400), ("request_not_a_string", 400)],
)
async def test_a_plugin_error_is_pushed_not_answered(name: str, code: int) -> None:
    """Janus calls the message a success; the refusal rides inside the push."""
    recorded = push_of(name)["plugindata"]["data"]

    def refuse(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "features":
            return [out[0], push_of(name)]
        return out

    async with gateway(rewrite=refuse) as (url, _, _requests):
        with pytest.raises(WebRTCError) as caught:
            async with session(url):
                pass
    assert caught.value.code == recorded["error_code"] == code
    assert caught.value.reason == recorded["error"]


async def test_a_plugin_that_says_nothing_is_reported_as_such() -> None:
    """The plugin's answer is a push, so a missing one is not a missing reply."""

    def mute(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "features":
            return out[:1]
        return out

    async with gateway(rewrite=mute) as (url, _, _requests):
        with pytest.raises(WebRTCError, match="plugin said nothing"):
            async with session(url, open_timeout=0.5):
                pass


async def test_a_watch_without_an_offer_is_a_response_error() -> None:
    """Nothing to answer means a plugin this release does not understand."""

    def strip(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "watch":
            push = {
                key: value for key, value in push_of("watch").items() if key != "jsep"
            }
            return [out[0], push]
        return out

    async with gateway(rewrite=strip) as (url, _, _requests):
        with pytest.raises(ResponseError, match="no offer to answer"):
            async with session(url):
                pass


async def test_features_answered_with_something_else_is_a_response_error() -> None:
    def swap(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message.get("body", {}).get("request") == "features":
            return [out[0], push_of("stop")]
        return out

    async with gateway(rewrite=swap) as (url, _, _requests):
        with pytest.raises(ResponseError, match="answered 'features' with something"):
            async with session(url):
                pass


async def test_a_create_without_an_id_is_a_response_error() -> None:
    def strip(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message["janus"] == "create":
            return [{key: value for key, value in out[0].items() if key != "data"}]
        return out

    async with gateway(rewrite=strip) as (url, _, _requests):
        with pytest.raises(ResponseError, match="without a session id"):
            async with session(url):
                pass


# --- While it runs --------------------------------------------------------


async def test_keepalive_is_acknowledged() -> None:
    async with gateway() as (url, seen, _requests):
        async with session(url) as rtc:
            await rtc.keepalive()
    assert any(message["janus"] == "keepalive" for message in seen)


async def test_a_keepalive_for_a_forgotten_session_reports_the_code() -> None:
    """A keepalive loop that outlives its session sees this and nothing else."""
    recorded = answer_of("keepalive_after_destroy")

    def forget(message: dict[str, Any], out: list[dict[str, Any]]) -> Any:
        if message["janus"] == "keepalive":
            return [{**recorded, "transaction": message["transaction"]}]
        return out

    async with gateway(rewrite=forget) as (url, _, _requests):
        async with session(url) as rtc:
            with pytest.raises(WebRTCError) as caught:
                await rtc.keepalive()
    assert caught.value.code == recorded["error"]["code"] == 458


async def test_request_keyframe_does_not_wait_for_an_answer() -> None:
    """The plugin sets a flag and pushes nothing; waiting would hang forever."""
    async with gateway() as (url, seen, _requests):
        async with session(url) as rtc:
            await asyncio.wait_for(rtc.request_keyframe(), timeout=5.0)
    assert "key_required" in bodies(seen)


async def test_the_session_keeps_itself_alive() -> None:
    """Janus drops a session silent for sixty seconds and the video with it."""
    async with gateway() as (url, seen, _requests):
        async with session(url, keepalive_interval=0.05):
            await asyncio.sleep(0.3)
    assert sum(message["janus"] == "keepalive" for message in seen) >= 2


async def test_events_hand_over_what_janus_said_unprompted() -> None:
    """`webrtcup` answers nothing the client sent, so this is where it lands."""
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            collected = [event async for event in _drain(rtc)]
    up = next(event for event in collected if event.janus == "webrtcup")
    assert up.sender == HANDLE_ID
    assert up.session_id == SESSION_ID


async def test_events_carry_the_plugin_pushes_the_negotiation_consumed() -> None:
    """A push is an event in its own right, and reading them costs nothing."""
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            collected = [event async for event in _drain(rtc)]
    pushes = [event for event in collected if event.janus == "event"]
    assert pushes[0].plugindata is not None
    assert pushes[0].plugindata.data.result is not None
    assert pushes[0].plugindata.data.result.status == "features"


async def test_events_do_not_repeat_the_answers_to_requests() -> None:
    """An acknowledgement is consumed where the request was made."""
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            await rtc.keepalive()
            await asyncio.sleep(0.05)
            collected = [event.janus async for event in _drain(rtc)]
    assert set(collected) == {"event", "webrtcup"}


async def _drain(rtc: WebRTCSession) -> AsyncIterator[WebRTCEvent]:
    """Yield the events already buffered and stop there."""
    while rtc._events:
        yield rtc._events.popleft()


async def test_pushes_nobody_is_waiting_for_do_not_pile_up() -> None:
    """The negotiation takes the three it asked for; nothing reads the rest."""
    with patch("aiopikvm._webrtc._PUSH_BUFFER", 2):
        rtc = session()
    for index in range(4):
        rtc._route({**push_of("watch"), "jsep": {"type": "offer", "sdp": str(index)}})
    kept = [rtc._pushes.get_nowait()[1] for _ in range(rtc._pushes.qsize())]
    assert [jsep["sdp"] for jsep in kept if jsep is not None] == ["2", "3"]


# --- Using it closed ------------------------------------------------------


async def test_nothing_works_before_the_block() -> None:
    rtc = session()
    with pytest.raises(WebRTCError, match="not open"):
        await rtc.keepalive()
    with pytest.raises(WebRTCError, match="not open"):
        await rtc.request_keyframe()
    with pytest.raises(WebRTCError, match="not open"):
        await anext(rtc.video())
    with pytest.raises(WebRTCError, match="not open"):
        await anext(rtc.events())


async def test_frames_before_the_block_are_not_reported_as_a_missing_decoder() -> None:
    """Without the extra there is no PyAV to import, and that is not the news."""
    rtc = session()
    with patch.dict(sys.modules, {"av.video.frame": None, "av.audio.frame": None}):
        with pytest.raises(WebRTCError, match="not open"):
            await anext(rtc.video())
        with pytest.raises(WebRTCError, match="not open"):
            await anext(rtc.audio())


async def test_the_session_can_be_opened_again() -> None:
    """Nothing from the first run is left to confuse the second."""
    rtc = session()
    async with gateway() as (url, seen, _requests):
        rtc._url = f"ws{url[4:]}/janus/ws"
        async with rtc:
            pass
        async with rtc:
            pass
    assert [message["transaction"] for message in seen[:2]] == [
        "aiopikvm-1",
        "aiopikvm-2",
    ]
    assert seen[9]["transaction"] == "aiopikvm-1"


# --- Frames ---------------------------------------------------------------

SHAPE: dict[str, Any] = step("frames")["frames"][0]


def a_frame() -> Any:
    """Build one video frame the shape the device sent."""
    from av.video.frame import VideoFrame

    return VideoFrame(
        width=SHAPE["width"], height=SHAPE["height"], format=SHAPE["format"]
    )


class Track:
    """A track that hands out a fixed number of frames and then ends."""

    kind = "video"

    def __init__(self, count: int) -> None:
        """Prepare a track.

        Args:
            count: How many frames to hand out before ending.
        """
        self.left = count

    async def recv(self) -> Any:
        """Hand over the next frame.

        Returns:
            The frame.

        Raises:
            MediaStreamError: Once the frames run out, the way aiortc reports
                a track that has ended.
        """
        from aiortc.mediastreams import MediaStreamError

        if self.left <= 0:
            raise MediaStreamError
        self.left -= 1
        return a_frame()


class Stalled:
    """A track that produces nothing and does not end on its own."""

    kind = "video"

    async def recv(self) -> Any:
        """Wait, and keep waiting.

        Returns:
            Nothing: the wait outlives everything but a cancellation.
        """
        await asyncio.Event().wait()


async def _pumped(rtc: WebRTCSession, count: int) -> None:
    """Attach a finite track to a session and let it drain."""
    rtc._on_track(Track(count))  # type: ignore[arg-type]
    await rtc._pumps["video"]


async def test_video_yields_the_frames_the_track_produced() -> None:
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            await _pumped(rtc, 3)
            frames = [frame async for frame in rtc.video()]
    assert len(frames) == 3
    assert frames[0].width == SHAPE["width"]
    assert frames[0].height == SHAPE["height"]
    assert frames[0].format.name == SHAPE["format"]


async def test_a_slow_consumer_loses_the_oldest_frames() -> None:
    """aiortc queues without a limit; this one keeps the newest and no more."""
    async with gateway() as (url, _, _requests):
        async with session(url, frame_buffer=2) as rtc:
            await _pumped(rtc, 10)
            frames = [frame async for frame in rtc.video()]
    assert len(frames) == 2


async def test_the_track_is_reachable_for_a_caller_that_wants_it() -> None:
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            await _pumped(rtc, 1)
            assert rtc.track() is not None
            assert rtc.track("audio") is None


async def test_a_second_track_of_a_kind_does_not_strand_the_first_pump() -> None:
    """The dict is the only handle on a pump, the teardown included."""
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            rtc._on_track(Stalled())  # type: ignore[arg-type]
            stranded = rtc._pumps["video"]
            await _pumped(rtc, 1)
            await asyncio.sleep(0)
            assert stranded.cancelled()
            assert [frame async for frame in rtc.video()] != []


async def test_audio_yields_nothing_when_the_device_sent_no_audio_track() -> None:
    async with gateway() as (url, _, _requests):
        async with session(url) as rtc:
            assert [frame async for frame in rtc.audio()] == []


# --- The models against the recording -------------------------------------


def _recorded_pushes() -> list[dict[str, Any]]:
    """Every plugin push in the recording."""
    return [
        recorded["push"]["plugindata"]["data"]
        for recorded in load_json("janus_session")["steps"]
        if recorded.get("push") is not None
    ]


def _recorded_events() -> list[dict[str, Any]]:
    """Every message the session buffers as an event.

    Which is not every message in the recording: an answer to something the
    client sent is consumed where it was sent, so `events()` never sees one
    and `WebRTCEvent` does not describe its ``transaction`` or its ``error``.
    What is left is the plugin's pushes and what Janus said unprompted.
    """
    out: list[dict[str, Any]] = []
    for recorded in load_json("janus_session")["steps"]:
        if recorded.get("push") is not None:
            out.append(recorded["push"])
        out += recorded.get("events", [])
    return out


@pytest.mark.parametrize("data", _recorded_pushes())
def test_plugin_pushes_parse_with_nothing_left_over(data: dict[str, Any]) -> None:
    assert undeclared_fields(WebRTCPluginEvent.model_validate(data)) == []


@pytest.mark.parametrize("data", _recorded_events())
def test_janus_messages_parse_with_nothing_left_over(data: dict[str, Any]) -> None:
    """A field nobody declared is invisible to callers instead of a loud error."""
    assert undeclared_fields(WebRTCEvent.model_validate(data)) == []


def test_the_plugin_never_answers_under_the_transaction_it_was_asked_on() -> None:
    """The whole reader design rests on this: pushes are routed by content."""
    shared = [
        recorded["push_shares_the_transaction"]
        for recorded in load_json("janus_session")["steps"]
        if recorded.get("push") is not None
    ]
    assert shared and not any(shared)
