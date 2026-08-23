"""Hand-record the Janus WebRTC signalling scenario fixture for #94.

Usage::

    PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret \\
        uv run python -m tests.fixtures.record_janus

As a module, not as a script: the output path comes from the same
``tests.fixtures`` the loader reads it through, and that import needs the
repository root on the path.

Read-only. It walks one whole session against ``/janus/ws`` — create, attach,
features, watch, answer, start, keepalive, key_required, stop, detach, destroy
— and records every message shape along the way, including the refusals the
happy path never shows.

Every plugin request is recorded twice over, because Janus answers it twice:
once synchronously, which is what the transaction gets back, and once as an
event the plugin *pushes* afterwards. Which of the two carries the request's
transaction is exactly the sort of thing worth pinning to a real device rather
than reading out of a header, so the recorder does not assume either way — it
routes pushes by their content and records the transaction each one arrived
with.

Two things are deliberately not stored. Frame payloads never are, for the same
reason the media recorder skips them: they are a picture of whatever is on the
attached host's screen. And every SDP goes through [`scrub_sdp`][scrub_sdp]
first — an offer carries the device's addresses, its DTLS fingerprint and the
ICE credentials of a live session, none of which belong in a repository.

Needs the ``webrtc`` extra (``uv sync --all-groups``), since building an answer
Janus will accept means a real peer connection.
"""

import asyncio
import contextlib
import json
import os
import re
import ssl
import sys
from typing import Any

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription

from tests.fixtures import DATA_DIR

URL = os.environ["PIKVM_URL"].rstrip("/")
USER = os.environ.get("PIKVM_USER", "admin")
PASSWD = os.environ["PIKVM_PASSWD"]
HEADERS = {"X-KVMD-User": USER, "X-KVMD-Passwd": PASSWD}
WS_URL = URL.replace("https://", "wss://").replace("http://", "ws://") + "/janus/ws"
PLUGIN = "janus.plugin.ustreamer"
SUBPROTOCOL = "janus-protocol"

TLS: ssl.SSLContext | None = None
if WS_URL.startswith("wss://"):
    # An untouched PiKVM serves a certificate it signed itself.
    TLS = ssl.create_default_context()
    TLS.check_hostname = False
    TLS.verify_mode = ssl.CERT_NONE

steps: list[dict[str, Any]] = []


def step(name: str, **kwargs: Any) -> None:
    """Record one step of the scenario and echo it.

    Args:
        name: Short label the tests look the step up by.
        **kwargs: Everything else the step carries — its description, the
            message that produced it, and whatever came back.
    """
    steps.append({"name": name, **kwargs})
    print(json.dumps(steps[-1], indent=1)[:900], "\n")


_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")


def scrub_sdp(sdp: str) -> str:
    """Strip everything session- or device-specific out of an SDP.

    What survives is the shape a parser sees: the media lines, the codecs and
    their parameters, the direction attributes, the extensions. What does not
    is anything that identifies the device or would let this session be
    replayed — addresses, the DTLS fingerprint, the ICE credentials, and the
    candidates, which are only the addresses again.

    Args:
        sdp: The SDP as it arrived.

    Returns:
        The same SDP with those values replaced by fixed placeholders.
    """
    out = []
    for line in sdp.splitlines():
        if line.startswith("o="):
            fields = line[2:].split()
            fields[1:3] = ["0", "0"]
            fields[-1] = "0.0.0.0"
            line = "o=" + " ".join(fields)
        elif line.startswith("a=candidate:") or line.startswith("a=end-of-candidates"):
            continue
        elif line.startswith("a=ice-ufrag:"):
            line = "a=ice-ufrag:XXXXXXXX"
        elif line.startswith("a=ice-pwd:"):
            line = "a=ice-pwd:XXXXXXXXXXXXXXXXXXXXXXXX"
        elif line.startswith("a=fingerprint:"):
            algo = line[len("a=fingerprint:") :].split(None, 1)[0]
            line = f"a=fingerprint:{algo} " + ":".join(["00"] * 32)
        elif line.startswith("s="):
            line = "s=-"
        line = _IPV4.sub("0.0.0.0", line)
        if line.startswith("c=IN IP6") or line.startswith("a=rtcp:"):
            line = _IPV6.sub("::", line)
        out.append(line)
    return "\r\n".join(out) + "\r\n"


def scrub(message: Any) -> Any:
    """Walk a Janus message and put every value worth hiding behind a marker.

    Session and handle ids are per-run counters and stay as they are — a test
    has to see that they are integers, and they mean nothing once the session
    is gone. An SDP is scrubbed. An ICE server URL is not recorded at all: it
    can name a host outside the device.

    Args:
        message: A decoded Janus message, or any part of one.

    Returns:
        A copy safe to commit.
    """
    if isinstance(message, dict):
        clean: dict[str, Any] = {}
        for key, value in message.items():
            if key == "sdp" and isinstance(value, str):
                clean[key] = scrub_sdp(value)
            elif key in ("url", "candidate") and isinstance(value, str):
                clean[key] = "<redacted>"
            else:
                clean[key] = scrub(value)
        return clean
    if isinstance(message, list):
        return [scrub(item) for item in message]
    return message


def leaks(text: str) -> list[str]:
    """Find anything in the finished recording that must not be committed.

    The same guard the capture tool has, for the same reason: a recording
    comes off somebody's actual device, and one stray error string carrying
    the host name is enough to put it in a repository forever. An SDP is
    scrubbed on the way in, but the text of an exception is whatever the
    library that raised it decided to say.

    Args:
        text: The whole recording, serialised.

    Returns:
        The names of the values that survived, empty when none did.
    """
    host = URL.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    found = []
    for name, value in (("PIKVM_PASSWD", PASSWD), ("PIKVM_USER", USER)):
        if value and value in text:
            found.append(name)
    for label in (host, *host.split(".")[:-1]):
        if label and len(label) > 3 and label in text:
            found.append(f"host label {label!r}")
    strays = {found_ip for found_ip in _IPV4.findall(text) if found_ip != "0.0.0.0"}
    found.extend(f"the address {stray}" for stray in sorted(strays))
    return found


def is_push(message: dict[str, Any]) -> bool:
    """Say whether a message is one the ustreamer plugin pushed itself.

    Janus wraps a plugin's synchronous return value the same way it wraps a
    push, so the two are told apart by what is inside: the plugin stamps
    everything it pushes with ``ustreamer: "event"``, and its synchronous
    return is a bare ``{"ok": true}``.

    Args:
        message: A decoded Janus message.

    Returns:
        Whether the plugin itself is speaking.
    """
    plugindata = message.get("plugindata")
    if not isinstance(plugindata, dict):
        return False
    data = plugindata.get("data")
    return isinstance(data, dict) and data.get("ustreamer") == "event"


class Signalling:
    """One Janus WebSocket, with everything on it routed three ways.

    A message that carries a transaction this client is waiting on answers a
    request. A message the plugin pushed is the plugin's own answer, which may
    or may not carry that same transaction — it is recorded either way and
    never assumed. Everything else is loose: ``webrtcup``, ``media``,
    ``hangup``, and whatever a newer Janus adds.

    Attributes:
        loose: Every message that was neither, in arrival order.
    """

    def __init__(self, socket: websockets.ClientConnection) -> None:
        """Start reading a socket that is already connected.

        Args:
            socket: The open Janus WebSocket.
        """
        self._socket = socket
        self._counter = 0
        self._waiting: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._pushes: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.loose: list[dict[str, Any]] = []
        self._reader = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        """Read the socket forever, handing each message to whoever wants it."""
        async for raw in self._socket:
            message = json.loads(raw)
            routed = False
            transaction = message.get("transaction")
            queue = self._waiting.get(transaction) if transaction else None
            if queue is not None:
                queue.put_nowait(message)
                routed = True
            if is_push(message):
                self._pushes.put_nowait(message)
                routed = True
            if not routed:
                self.loose.append(message)

    async def close(self) -> None:
        """Stop reading."""
        self._reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader

    async def send(self, **body: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        """Send one message and wait for the answer carrying its transaction.

        Args:
            **body: The message, minus the transaction this adds.

        Returns:
            What was sent and the first thing that came back for it.
        """
        self._counter += 1
        transaction = f"aiopikvm-{self._counter}"
        message = {**body, "transaction": transaction}
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._waiting[transaction] = queue
        await self._socket.send(json.dumps(message))
        answer = await asyncio.wait_for(queue.get(), timeout=20.0)
        return message, answer

    async def push(self, timeout: float = 20.0) -> dict[str, Any]:
        """Wait for the plugin's next push.

        Args:
            timeout: How long to wait.

        Returns:
            The message the plugin pushed.
        """
        return await asyncio.wait_for(self._pushes.get(), timeout=timeout)

    async def settle(self, seconds: float = 2.0) -> list[dict[str, Any]]:
        """Let loose messages arrive, then hand over what did.

        Args:
            seconds: How long to wait.

        Returns:
            Everything that arrived loose since the last call, oldest first.
        """
        await asyncio.sleep(seconds)
        arrived, self.loose = self.loose, []
        return arrived


def body_of(body: bytes) -> Any:
    """Decode a refusal's body without assuming it is JSON.

    kvmd answers most things with its envelope, but a handshake it refuses
    before routing is answered by whatever is in front — which may be plain
    text or a page. The recording keeps whichever it turns out to be, so a
    test can assert on the real thing.

    Args:
        body: The bytes that came with the response.

    Returns:
        The decoded JSON, or a short text excerpt when it is not JSON.
    """
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"text": body.decode("utf-8", "replace").strip()[:200]}


async def record_refusals() -> None:
    """Record what happens to a socket that should not be opened at all."""
    try:
        async with websockets.connect(
            WS_URL, subprotocols=[SUBPROTOCOL], proxy=None, ssl=TLS
        ) as socket:
            # Janus says nothing to a client that has not spoken, so on a
            # device with auth switched off this waits out the timeout and
            # the step below is simply not recorded.
            await asyncio.wait_for(socket.recv(), timeout=5.0)
    except websockets.exceptions.InvalidStatus as exc:
        step(
            "upgrade_unauthenticated",
            description=(
                "No credentials. Janus never sees the request: kvmd's auth "
                "sits in front of it, so the refusal is the same envelope "
                "every other unauthenticated call gets, and there is no "
                "socket to read an error off."
            ),
            request={"method": "GET", "path": "/janus/ws", "headers": []},
            status=exc.response.status_code,
            content_type=exc.response.headers.get("content-type", ""),
            response=body_of(exc.response.body),
        )
    except TimeoutError:
        print("unauthenticated upgrade was accepted; step not recorded\n")

    try:
        async with websockets.connect(
            WS_URL, additional_headers=HEADERS, proxy=None, ssl=TLS
        ) as socket:
            await asyncio.wait_for(socket.recv(), timeout=5.0)
    except Exception as exc:  # Whatever it turns out to be is the point.
        step(
            "upgrade_without_subprotocol",
            description=(
                "Authenticated, but the handshake does not ask for "
                f"`{SUBPROTOCOL}`. Janus serves its WebSocket transport only "
                "under that subprotocol, so a client that does not name it "
                "gets no usable socket. Not a replayable mock: this records "
                "the exception the handshake raised rather than a response, "
                "and the session always names the subprotocol, so there is "
                "no way through this client to send this request. The HTTP "
                "502 in the excerpt is still recorded evidence, and the "
                "gateway test reads its status out of it."
            ),
            request={"method": "GET", "path": "/janus/ws", "subprotocols": []},
            error=type(exc).__name__,
            error_excerpt=str(exc)[:160],
        )


async def record_session(sig: Signalling) -> tuple[int, int]:
    """Record everything up to a handle attached to the ustreamer plugin.

    Args:
        sig: The signalling socket.

    Returns:
        The session id and the handle id.
    """
    sent, got = await sig.send(janus="create")
    step(
        "create",
        description=(
            "A Janus session. `data.id` is what every later message has to "
            "carry as `session_id`, and the session dies about sixty seconds "
            "after the last message unless something keeps sending "
            "`keepalive`."
        ),
        request=scrub(sent),
        response=scrub(got),
    )
    session = int(got["data"]["id"])

    sent, got = await sig.send(
        janus="attach", session_id=session, plugin="janus.plugin.nope"
    )
    step(
        "attach_unknown_plugin",
        description=(
            "A plugin name Janus does not have. The error is Janus's own "
            'shape — `janus: "error"` with a numbered `error` object — and '
            "nothing like the kvmd envelope the rest of the API answers with."
        ),
        request=scrub(sent),
        response=scrub(got),
    )

    sent, got = await sig.send(janus="attach", session_id=session, plugin=PLUGIN)
    step(
        "attach",
        description=(
            f"The ustreamer plugin, `{PLUGIN}`. `data.id` is the handle every "
            "plugin message has to carry as `handle_id`; a session can hold "
            "several."
        ),
        request=scrub(sent),
        response=scrub(got),
    )
    return session, int(got["data"]["id"])


async def plugin_message(
    sig: Signalling,
    session: int,
    handle: int,
    body: dict[str, Any],
    jsep: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Send one plugin message and collect both halves of the answer.

    Args:
        sig: The signalling socket.
        session: The session id.
        handle: The handle id.
        body: The request body.
        jsep: The SDP to carry alongside it, if any.

    Returns:
        What was sent, what came back on the transaction, and what the plugin
        pushed — ``None`` when it pushed nothing before the timeout.
    """
    message: dict[str, Any] = {
        "janus": "message",
        "session_id": session,
        "handle_id": handle,
        "body": body,
    }
    if jsep is not None:
        message["jsep"] = jsep
    sent, answer = await sig.send(**message)
    push: dict[str, Any] | None = None
    with contextlib.suppress(TimeoutError):
        push = await sig.push(timeout=10.0)
    return sent, answer, push


def record_plugin_step(
    name: str,
    description: str,
    sent: dict[str, Any],
    answer: dict[str, Any],
    push: dict[str, Any] | None,
) -> None:
    """Record one plugin message and both halves of its answer.

    Args:
        name: Short label the tests look the step up by.
        description: What the step shows.
        sent: The message as it went out.
        answer: What came back carrying its transaction.
        push: What the plugin pushed, if anything.
    """
    step(
        name,
        description=description,
        request=scrub(sent),
        response=scrub(answer),
        push=scrub(push) if push is not None else None,
        push_shares_the_transaction=(
            push.get("transaction") == sent["transaction"] if push else None
        ),
    )


async def record_bad_requests(sig: Signalling, session: int, handle: int) -> None:
    """Record what the plugin says to messages it cannot act on.

    Args:
        sig: The signalling socket.
        session: The session id.
        handle: The handle id.
    """
    for name, body, description in (
        (
            "request_unknown",
            {"request": "nope"},
            "A request name the plugin does not implement. It answers 405, "
            "and the error rides inside `plugindata.data` of a pushed event "
            "rather than at the top level — a plugin error is a successful "
            "Janus message.",
        ),
        (
            "request_missing",
            {},
            "A body with no `request` at all: 400. Same place, same shape.",
        ),
        (
            "request_not_a_string",
            {"request": 42},
            "A `request` that is not a string: 400 again, with its own text.",
        ),
    ):
        sent, answer, push = await plugin_message(sig, session, handle, body)
        record_plugin_step(name, description, sent, answer, push)


async def record_features(sig: Signalling, session: int, handle: int) -> None:
    """Record the plugin's feature announcement.

    Args:
        sig: The signalling socket.
        session: The session id.
        handle: The handle id.
    """
    sent, answer, push = await plugin_message(
        sig, session, handle, {"request": "features"}
    )
    record_plugin_step(
        "features",
        "What this build of ustreamer can do. `audio` and `mic` say whether "
        "the device has them wired up at all, and `ice` carries the STUN or "
        "TURN server kvmd was configured with — recorded as a marker here, "
        "because it can name a host that is not the device.",
        sent,
        answer,
        push,
    )


async def record_watch(sig: Signalling, session: int, handle: int) -> str:
    """Ask for video and record the offer that comes back.

    Args:
        sig: The signalling socket.
        session: The session id.
        handle: The handle id.

    Returns:
        The SDP the plugin offered, unscrubbed.

    Raises:
        RuntimeError: The plugin answered without an offer, so there is
            nothing to negotiate against and the rest of the recording cannot
            happen.
    """
    sent, answer, push = await plugin_message(
        sig,
        session,
        handle,
        {
            "request": "watch",
            "params": {
                "orientation": 0,
                "audio": False,
                "mic": False,
                "camera": False,
            },
        },
    )
    record_plugin_step(
        "watch",
        "The request that starts a negotiation. The ustreamer plugin is the "
        'offerer, so what it pushes back is `jsep.type == "offer"` and the '
        "client owes it an answer — the opposite way round from most WebRTC "
        "code. `result.status` is `started` even though nothing is flowing "
        "yet.",
        sent,
        answer,
        push,
    )
    if push is None or not isinstance(push.get("jsep"), dict):
        raise RuntimeError("the plugin answered 'watch' with no offer")
    sdp = push["jsep"]["sdp"]
    if not isinstance(sdp, str):
        raise RuntimeError("the plugin's offer carried no SDP")
    return sdp


async def record_media(sig: Signalling, session: int, handle: int, offer: str) -> None:
    """Answer the offer, start the stream and record what flows.

    Args:
        sig: The signalling socket.
        session: The session id.
        handle: The handle id.
        offer: The SDP the plugin offered, as it arrived.
    """
    pc = RTCPeerConnection()
    frames: list[dict[str, Any]] = []
    pullers: list[asyncio.Task[None]] = []
    first = asyncio.Event()

    @pc.on("track")
    def on_track(track: Any) -> None:
        async def pull() -> None:
            for _ in range(8):
                frame = await track.recv()
                frames.append(
                    {
                        "kind": track.kind,
                        "width": getattr(frame, "width", None),
                        "height": getattr(frame, "height", None),
                        "format": getattr(getattr(frame, "format", None), "name", None),
                        "pts_is_int": isinstance(frame.pts, int),
                    }
                )
                first.set()

        pullers.append(asyncio.create_task(pull()))

    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
    answer_sdp = await pc.createAnswer()
    assert answer_sdp is not None
    await pc.setLocalDescription(answer_sdp)

    sent, answer, push = await plugin_message(
        sig,
        session,
        handle,
        {"request": "start"},
        jsep={"type": "answer", "sdp": pc.localDescription.sdp},
    )
    record_plugin_step(
        "start",
        "The answer, carried by `start`. From here Janus has both halves of "
        "the negotiation and begins the DTLS handshake; the plugin pushes "
        '`status: "started"` a second time, now with no jsep. The SDP in the '
        "request is this client's own answer, scrubbed the same way the offer "
        "is.",
        sent,
        answer,
        push,
    )

    sent, ack = await sig.send(
        janus="trickle",
        session_id=session,
        handle_id=handle,
        candidate={"completed": True},
    )
    step(
        "trickle_completed",
        description=(
            "This client gathers every candidate before it answers, so the "
            "answer above is already complete and the only trickle it has to "
            "send is the one that says so. Janus acknowledges a trickle and "
            "never events it."
        ),
        request=scrub(sent),
        response=scrub(ack),
    )

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(first.wait(), timeout=10.0)
    step(
        "without_a_viewer",
        description=(
            "The negotiation finished and no picture came. kvmd runs "
            "ustreamer only while a session asked to be counted as a viewer "
            "— `GET /api/ws?stream=1` — and the Janus plugin reads its "
            "frames out of ustreamer's memory sink, so with the streamer "
            "stopped there is simply nothing to send. Janus still completes "
            "the handshake and still says `webrtcup`: nothing in the "
            "signalling reports the silence."
        ),
        events=[scrub(item) for item in await sig.settle(2.0)],
        frames=list(frames),
    )

    async with websockets.connect(
        WS_URL.replace("/janus/ws", "/api/ws?stream=1"),
        additional_headers=HEADERS,
        proxy=None,
        ssl=TLS,
        max_size=None,
    ):
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(first.wait(), timeout=25.0)
        step(
            "session_events",
            description=(
                "Everything Janus sent on its own once a kvmd viewer socket "
                "was open beside this one and the streamer came back up — "
                "which is nothing. `webrtcup` had already been sent before "
                "the picture existed, and Janus events `media` for what it "
                "*receives*: this session only receives, so no `media` ever "
                "arrives and the frames below are the only evidence that "
                "anything started flowing. Kept as documentation, not as a "
                "mock: an empty recording is a claim about the device, and "
                "there is nothing in it for a test to replay."
            ),
            events=[scrub(item) for item in await sig.settle(3.0)],
        )
        step(
            "frames",
            description=(
                "Decoded video off the peer connection. Only the shape is "
                "recorded — the pixels are the attached host's screen, the "
                "same reason the media fixture stores frame lengths and "
                "nothing else."
            ),
            frames=list(frames),
        )

    sent, answer, push = await plugin_message(
        sig, session, handle, {"request": "key_required"}
    )
    record_plugin_step(
        "key_required",
        "Asks the encoder for a keyframe. The plugin sets a flag and says "
        "nothing further, so there is no push to wait for and a caller that "
        "waits for one waits forever.",
        sent,
        answer,
        push,
    )

    sent, answer, push = await plugin_message(sig, session, handle, {"request": "stop"})
    record_plugin_step(
        "stop",
        "Ends the stream for this handle. The plugin answers "
        '`status: "stopped"`, and Janus tears the peer connection down behind '
        "it.",
        sent,
        answer,
        push,
    )
    step(
        "after_stop",
        description=(
            "What Janus sends once the peer connection is gone. Kept as "
            "documentation, not as a mock: an empty recording is a claim "
            "about the device, and there is nothing in it for a test to "
            "replay."
        ),
        events=[scrub(item) for item in await sig.settle(3.0)],
    )
    for puller in pullers:
        puller.cancel()
    await pc.close()


async def record_teardown(sig: Signalling, session: int, handle: int) -> None:
    """Record keepalive, detach, destroy and the errors after them.

    Args:
        sig: The signalling socket.
        session: The session id.
        handle: The handle id.
    """
    sent, got = await sig.send(janus="keepalive", session_id=session)
    step(
        "keepalive",
        description=(
            "What keeps the session from timing out. Janus answers `ack` and "
            "nothing else; the web UI sends one every twenty-five seconds."
        ),
        request=scrub(sent),
        response=scrub(got),
    )

    sent, got = await sig.send(janus="detach", session_id=session, handle_id=handle)
    step(
        "detach",
        description="Drops the handle. The session stays.",
        request=scrub(sent),
        response=scrub(got),
    )

    sent, answer, push = await plugin_message(
        sig, session, handle, {"request": "features"}
    )
    record_plugin_step(
        "message_to_a_dead_handle",
        "The same message again, to the handle just detached. This is the "
        'top-level error shape — `janus: "error"` — as opposed to a plugin '
        "error, which arrives inside `plugindata` of a message Janus "
        "considers successful.",
        sent,
        answer,
        push,
    )

    sent, got = await sig.send(janus="destroy", session_id=session)
    step(
        "destroy",
        description="Ends the session. Every handle under it goes with it.",
        request=scrub(sent),
        response=scrub(got),
    )

    sent, got = await sig.send(janus="keepalive", session_id=session)
    step(
        "keepalive_after_destroy",
        description=(
            "A session id Janus no longer knows. Worth pinning because a "
            "keepalive loop that outlives its session sees this and nothing "
            "else — the socket stays open and healthy underneath it."
        ),
        request=scrub(sent),
        response=scrub(got),
    )


async def main() -> int:
    """Record the whole scenario and write it next to the other fixtures.

    Returns:
        The process exit status.
    """
    await record_refusals()

    async with websockets.connect(
        WS_URL,
        additional_headers=HEADERS,
        subprotocols=[SUBPROTOCOL],
        proxy=None,
        ssl=TLS,
        max_size=None,
    ) as socket:
        step(
            "upgrade",
            description=(
                "The handshake that works: kvmd credentials plus the "
                f"`{SUBPROTOCOL}` subprotocol, which the server echoes back. "
                "Nothing arrives until the client speaks first — unlike the "
                "kvmd event socket, Janus has nothing to say to a session "
                "that does not exist yet."
            ),
            request={
                "method": "GET",
                "path": "/janus/ws",
                "subprotocols": [SUBPROTOCOL],
            },
            subprotocol=socket.subprotocol,
        )
        sig = Signalling(socket)
        try:
            session, handle = await record_session(sig)
            await record_bad_requests(sig, session, handle)
            await record_features(sig, session, handle)
            offer = await record_watch(sig, session, handle)
            await record_media(sig, session, handle, offer)
            await record_teardown(sig, session, handle)
        finally:
            await sig.close()

    payload = {
        "description": (
            "A whole Janus WebRTC session against /janus/ws, hand-recorded. "
            "The capture tool cannot reach any of this: Janus speaks neither "
            "HTTP nor the kvmd envelope here, half of what matters is a "
            "refusal, and the interesting messages arrive unprompted. The "
            "session also pins the one thing no amount of reading the "
            "protocol reveals — a negotiation that succeeds in every visible "
            "way still delivers no picture until a kvmd viewer socket is "
            "open beside it, because that is what keeps ustreamer running. "
            "Every SDP is scrubbed — addresses, DTLS fingerprints and ICE "
            "credentials are replaced with placeholders — and frame payloads "
            "are not stored at all."
        ),
        "recorded_with": "tests/fixtures/record_janus.py (see the README)",
        "steps": steps,
    }
    serialised = json.dumps(payload, indent=2, ensure_ascii=False)
    if found := leaks(serialised):
        print(f"refusing to write: the recording still contains {', '.join(found)}")
        return 1

    path = DATA_DIR / "janus_session.json"
    path.write_text(serialised + "\n", encoding="utf-8")
    print(f"wrote {path.resolve()}")
    print("the manifest is not updated; see tests/fixtures/README.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
