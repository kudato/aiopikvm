"""Hand-record the live-video scenario fixture for #84.

Read-only. It opens one event socket with ``stream=True`` so that ustreamer is
running, then reads ustreamer's own API and the kvmd-media daemon. Frame
payloads are never stored: an MJPEG part and an H.264 frame are a picture of
whatever is on the attached host's screen, the same reason the capture tool
skips ``/api/streamer/snapshot``. Only the sizes and the first few bytes go in.
"""

import asyncio
import json
import os
import sys
from typing import Any

import httpx
import websockets

from aiopikvm import PiKVM

URL = os.environ["PIKVM_URL"].rstrip("/")
USER = os.environ.get("PIKVM_USER", "admin")
PASSWD = os.environ["PIKVM_PASSWD"]
HEADERS = {"X-KVMD-User": USER, "X-KVMD-Passwd": PASSWD}
KEY = "aiopikvm-capture"

steps: list[dict[str, Any]] = []


def step(name: str, **kwargs: Any) -> None:
    """Record one step of the scenario and echo it.

    Args:
        name: Short label the tests look the step up by.
        **kwargs: Everything else the step carries — its description, the
            request that produced it, and whatever came back.
    """
    steps.append({"name": name, **kwargs})
    print(json.dumps(steps[-1], indent=1)[:900], "\n")


def parts_of(body: bytes, boundary: bytes, want: int) -> list[dict[str, Any]]:
    """Split a multipart body into part headers plus a summary of each payload.

    Args:
        body: However much of the stream has arrived.
        boundary: What separates the parts, without the leading dashes.
        want: How many whole parts are enough.

    Returns:
        One entry per whole part found, up to *want*.
    """
    found: list[dict[str, Any]] = []
    buf = body
    while len(found) < want:
        start = buf.find(b"--" + boundary)
        if start < 0:
            break
        head_end = buf.find(b"\r\n\r\n", start)
        if head_end < 0:
            break
        head = buf[start + len(boundary) + 2 : head_end].decode("latin-1")
        headers = {}
        for line in head.split("\r\n"):
            if ":" in line:
                name, _, value = line.partition(":")
                headers[name.strip()] = value.strip()
        length = int(headers.get("Content-Length", "-1"))
        if length < 0 or len(buf) < head_end + 4 + length:
            break
        data = buf[head_end + 4 : head_end + 4 + length]
        found.append(
            {
                "headers": headers,
                "data_len": len(data),
                "data_head": data[:4].hex(),
            }
        )
        buf = buf[head_end + 4 + length :]
    return found


async def collect_parts(resp: httpx.Response, want: int) -> list[dict[str, Any]]:
    """Read a streaming response until it has handed over *want* whole parts.

    Args:
        resp: The streaming response, its body still unread.
        want: How many parts to wait for.

    Returns:
        What [`parts_of`][parts_of] made of them.
    """
    ctype = resp.headers.get("content-type", "")
    boundary = ctype.partition("boundary=")[2].encode()
    buf = b""
    async for chunk in resp.aiter_bytes():
        buf += chunk
        if len(parts_of(buf, boundary, want)) >= want:
            break
    return parts_of(buf, boundary, want)


async def read_stream(http: httpx.AsyncClient, path: str, want: int) -> dict[str, Any]:
    """Open an MJPEG stream and record what its first parts looked like.

    Args:
        http: Client already pointed at the device, credentials included.
        path: Path and query to open.
        want: How many parts to record.

    Returns:
        The fields of a scenario step that describe the response.
    """
    async with http.stream("GET", path) as resp:
        return {
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "parts": await collect_parts(resp, want),
        }


async def main() -> int:
    """Record the whole scenario and write it next to the other fixtures.

    Returns:
        The process exit status.
    """
    async with PiKVM(URL, user=USER, passwd=PASSWD, timeout=30.0) as kvm:
        async with httpx.AsyncClient(
            base_url=URL, headers=HEADERS, verify=False, timeout=20.0
        ) as http:
            await asyncio.sleep(2.0)
            r = await http.get("/streamer/state")
            step(
                "state_stopped",
                description=(
                    "ustreamer's own state with nobody watching. kvmd stops the "
                    "process when no session asks for video, and nginx has "
                    "nothing to proxy to, so this is an nginx page rather than "
                    "a kvmd envelope."
                ),
                request={"method": "GET", "path": "/streamer/state"},
                status=r.status_code,
                content_type=r.headers.get("content-type", ""),
                body_excerpt=r.text.strip().splitlines()[1][:80] if r.text else "",
            )

        async with kvm.ws(stream=True) as event_ws:
            # Something has to read the socket. websockets stops reading
            # the transport once its inbound queue fills, its own keepalive
            # pong then goes unread, and the connection is closed about 40
            # seconds in — taking the streamer with it.
            async def drain() -> None:
                async for _ in event_ws.events():
                    pass

            reader = asyncio.create_task(drain())
            await asyncio.sleep(5.0)
            async with httpx.AsyncClient(
                base_url=URL, headers=HEADERS, verify=False, timeout=20.0
            ) as http:
                r = await http.get("/streamer/state")
                step(
                    "state_idle",
                    description=(
                        "The same read while a session is watching. The result "
                        "is what kvmd relays verbatim into the `streamer` block "
                        "of `GET /api/streamer` — it reads `/state` and hands "
                        "the `result` through untouched."
                    ),
                    request={"method": "GET", "path": "/streamer/state"},
                    status=r.status_code,
                    content_type=r.headers.get("content-type", ""),
                    response=r.json(),
                )

                step(
                    "stream_plain",
                    description=(
                        "Two parts of the MJPEG stream, default query. Only the "
                        "part headers are recorded: the data is a picture of "
                        "the attached host's screen, so its length and first "
                        "four bytes (JPEG SOI plus an APP1 marker) stand in."
                    ),
                    request={"method": "GET", "path": "/streamer/stream"},
                    **await read_stream(http, "/streamer/stream", 2),
                )

                step(
                    "stream_extra_headers",
                    description=(
                        "The same stream with `extra_headers=1`, which adds "
                        "ustreamer's own annotations to every part."
                    ),
                    request={
                        "method": "GET",
                        "path": "/streamer/stream",
                        "params": {"extra_headers": 1},
                    },
                    **await read_stream(http, "/streamer/stream?extra_headers=1", 2),
                )

                step(
                    "stream_zero_data",
                    description=(
                        "`zero_data=1` keeps the part headers and drops the "
                        "JPEG data, which is what makes a frame-timing reader "
                        "cheap."
                    ),
                    request={
                        "method": "GET",
                        "path": "/streamer/stream",
                        "params": {"zero_data": 1, "extra_headers": 1},
                    },
                    **await read_stream(
                        http, "/streamer/stream?zero_data=1&extra_headers=1", 2
                    ),
                )

                async with http.stream(
                    "GET",
                    "/streamer/stream?advance_headers=1&extra_headers=1&zero_data=1",
                ) as resp:
                    adv = b""
                    async for chunk in resp.aiter_bytes():
                        adv += chunk
                        if adv.count(b"--boundarydonotcross") >= 3:
                            break
                    adv_status = resp.status_code
                    adv_ctype = resp.headers.get("content-type", "")
                step(
                    "stream_advance_headers",
                    description=(
                        "`advance_headers=1` is a Chromium workaround: "
                        "ustreamer sends the next part's boundary and headers "
                        "as soon as the current part's data is out, before it "
                        "has captured the frame they belong to. It cannot know "
                        "the size or the statistics of a frame that does not "
                        "exist yet, so the part headers lose `Content-Length` "
                        "*and* every `X-UStreamer-*` header, even though "
                        "`extra_headers=1` was asked for alongside. A reader "
                        "that finds parts by their declared length cannot "
                        "follow this stream at all, which is why the client "
                        "does not offer the flag. Recorded with `zero_data=1` "
                        "so that the bytes below are the framing and nothing "
                        "of the host's screen."
                    ),
                    request={
                        "method": "GET",
                        "path": "/streamer/stream",
                        "params": {
                            "advance_headers": 1,
                            "extra_headers": 1,
                            "zero_data": 1,
                        },
                    },
                    status=adv_status,
                    content_type=adv_ctype,
                    raw=adv[:400].decode("latin-1"),
                )

                async with http.stream(
                    "GET", "/streamer/stream?dual_final_frames=1&zero_data=1"
                ) as resp:
                    dual = b""
                    async for chunk in resp.aiter_bytes():
                        dual += chunk
                        if dual.count(b"--boundarydonotcross") >= 2:
                            break
                    dual_status = resp.status_code
                step(
                    "stream_dual_final_frames",
                    description=(
                        "`dual_final_frames=1` is the Safari workaround, and "
                        "the other flag the client does not offer. It keeps "
                        "`Content-Length` and only repeats the last part of a "
                        "series, so it parses fine — it just exists for a "
                        "renderer this client does not have."
                    ),
                    request={
                        "method": "GET",
                        "path": "/streamer/stream",
                        "params": {"dual_final_frames": 1, "zero_data": 1},
                    },
                    status=dual_status,
                    raw=dual[:300].decode("latin-1"),
                )

                stop = asyncio.Event()

                async def hold() -> None:
                    async with http.stream(
                        "GET", f"/streamer/stream?key={KEY}&extra_headers=1"
                    ) as resp:
                        async for _ in resp.aiter_bytes():
                            if stop.is_set():
                                break

                task = asyncio.create_task(hold())
                await asyncio.sleep(4.0)
                r = await http.get("/streamer/state")
                step(
                    "state_with_client",
                    description=(
                        "The state again with one named stream client "
                        "connected. `clients_stat` is keyed by an id ustreamer "
                        "assigns, and each entry echoes the query the client "
                        "connected with — `key` is what a caller passes to find "
                        "its own row."
                    ),
                    request={"method": "GET", "path": "/streamer/state"},
                    status=r.status_code,
                    response=r.json(),
                )
                stop.set()
                task.cancel()
                await asyncio.sleep(0.5)

                async with http.stream(
                    "GET", "/streamer/stream?extra_headers=zzz"
                ) as resp:
                    bad_parts = await collect_parts(resp, 1)
                    bad_status = resp.status_code
                    bad_ctype = resp.headers.get("content-type", "")
                step(
                    "stream_bad_flag",
                    description=(
                        "ustreamer parses the flags itself and has no "
                        "validator: a value it does not understand reads as "
                        "off, and the stream starts anyway."
                    ),
                    request={
                        "method": "GET",
                        "path": "/streamer/stream",
                        "params": {"extra_headers": "zzz"},
                    },
                    status=bad_status,
                    content_type=bad_ctype,
                    parts=bad_parts,
                )

                r = await http.get("/streamer/nope")
                step(
                    "not_found",
                    description=(
                        "A path ustreamer does not serve. It answers 404 with "
                        "an HTML page of its own — nothing under /streamer "
                        "speaks the kvmd envelope on the way out, so an error "
                        "here carries no `error` field to match on. The 502 in "
                        "the first step is the other shape: that one is nginx "
                        "with no upstream to reach."
                    ),
                    request={"method": "GET", "path": "/streamer/nope"},
                    status=r.status_code,
                    content_type=r.headers.get("content-type", ""),
                    body_excerpt=r.text[:120].replace("\n", " ").strip(),
                )

                r = await http.get("/api/media")
                step(
                    "media_state",
                    description=(
                        "What the kvmd-media daemon offers. `video` holds one "
                        "entry per configured source; the recording device has "
                        "H.264 only, and the metadata is the SDP "
                        "`profile-level-id` kvmd hardcodes."
                    ),
                    request={"method": "GET", "path": "/api/media"},
                    status=r.status_code,
                    response=r.json(),
                )

            ws_url = URL.replace("https://", "wss://").replace("http://", "ws://")

            frames: list[dict[str, Any]] = []
            async with websockets.connect(
                f"{ws_url}/api/media/ws?video=h264",
                additional_headers=HEADERS,
                proxy=None,
                max_size=None,
            ) as ws:
                await ws.send(b"\x01")  # Ask for a keyframe to open with.
                for _ in range(4):
                    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    frames.append(
                        {
                            "type": "binary",
                            "data_len": len(msg),
                            "data_head": msg[:5].hex(),
                        }
                    )
            step(
                "media_ws_pure",
                description=(
                    "The pure socket: `?video=<format>` starts the stream "
                    "during the handshake and every binary message afterwards "
                    "is one frame of raw video, with no operation byte and no "
                    "JSON at all. The H.264 is Annex B — `00000001` start code, "
                    "then the NAL header, `27` for the SPS that opens a "
                    "keyframe and `21` for a delta frame. Payloads are not "
                    "recorded: they are the host's screen."
                ),
                request={
                    "method": "GET",
                    "path": "/api/media/ws",
                    "params": {"video": "h264"},
                },
                frames=frames,
            )

            frames = []
            async with websockets.connect(
                f"{ws_url}/api/media/ws",
                additional_headers=HEADERS,
                proxy=None,
                max_size=None,
            ) as ws:
                frames.append({"type": "text", "msg": json.loads(await ws.recv())})
                await ws.send(b"\x00")
                await ws.send(
                    json.dumps(
                        {
                            "event_type": "start",
                            "event": {"type": "video", "format": "h264"},
                        }
                    )
                )
                for _ in range(4):
                    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    if isinstance(msg, str):
                        frames.append({"type": "text", "msg": json.loads(msg)})
                    else:
                        frames.append(
                            {
                                "type": "binary",
                                "op": msg[0],
                                "data_len": len(msg),
                                "data_head": msg[:6].hex(),
                            }
                        )
                await ws.send(
                    json.dumps(
                        {
                            "event_type": "start",
                            "event": {"type": "video", "format": "jpeg"},
                        }
                    )
                )
                await ws.send(json.dumps({"event_type": "nope", "event": {}}))
                await ws.send(b"\x7f")
                await ws.send(b"\x00")
                for _ in range(6):
                    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    if isinstance(msg, str):
                        frames.append({"type": "text", "msg": json.loads(msg)})
                    else:
                        frames.append(
                            {
                                "type": "binary",
                                "op": msg[0],
                                "data_len": len(msg),
                                "data_head": msg[:6].hex(),
                            }
                        )
                        if msg[0] == 255:
                            break
            step(
                "media_ws_regular",
                description=(
                    "The regular socket, which is what the web UI opens: no "
                    "query, so the first thing it sends is the same `media` "
                    "block `GET /api/media` returns, and nothing streams until "
                    "a `start` event asks for a format. Frames then arrive "
                    "under operation 1, with a keyframe flag byte before the "
                    "data. Operation 0 is answered with operation 255. A "
                    "`start` for a format the daemon does not have, an event "
                    "type it has no handler for, and an operation number it "
                    "has no handler for are all ignored in silence — the only "
                    "frames after them are the video and the second pong."
                ),
                request={"method": "GET", "path": "/api/media/ws"},
                frames=frames,
            )

            for name, value in (("media_ws_jpeg", "jpeg"), ("media_ws_unknown", "zzz")):
                try:
                    async with websockets.connect(
                        f"{ws_url}/api/media/ws?video={value}",
                        additional_headers=HEADERS,
                        proxy=None,
                        max_size=None,
                    ):
                        pass
                except websockets.exceptions.InvalidStatus as exc:
                    step(
                        name,
                        description=(
                            f"`video={value}` names a format this daemon does "
                            "not serve, so the upgrade is refused before the "
                            "socket exists and the answer is an ordinary kvmd "
                            "error envelope."
                        ),
                        request={
                            "method": "GET",
                            "path": "/api/media/ws",
                            "params": {"video": value},
                        },
                        status=exc.response.status_code,
                        content_type=exc.response.headers.get("content-type", ""),
                        response=json.loads(exc.response.body),
                    )

            reader.cancel()

    payload = {
        "description": (
            "Live video, hand-recorded: ustreamer's own API under /streamer "
            "and the kvmd-media daemon under /api/media. The capture tool "
            "records neither — it only stores GETs that succeed, and half of "
            "what matters here is a refusal, a WebSocket or a stream that "
            "never ends. Frame payloads are deliberately absent; they are a "
            "picture of the attached host's screen."
        ),
        "recorded_with": "tests/fixtures/record_media.py (see the README)",
        "steps": steps,
    }
    path = os.path.join(os.path.dirname(__file__), "media_stream.json")
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
