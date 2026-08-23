"""StreamerResource tests."""

import copy
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from aiopikvm import (
    APIError,
    ConfigurationError,
    PiKVM,
    ResponseError,
    UnavailableError,
)
from tests.fixtures import load_json

OK = {"ok": True, "result": {}}


def _state(**overrides: Any) -> dict[str, Any]:
    """The captured state with the top-level blocks replaced."""
    body = copy.deepcopy(load_json("streamer"))
    body["result"].update(overrides)
    return body


async def test_get_state_running(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer").mock(
        return_value=httpx.Response(200, json=load_json("streamer"))
    )
    state = await client.streamer.get_state()
    assert state.streamer is not None
    assert state.streamer.source.online is True
    assert state.streamer.source.resolution.width == 1920
    assert state.streamer.source.resolution.height == 1080
    assert state.streamer.h264 is not None
    # The capture asks kvmd for video, which is what starts the streamer,
    # but it never reads the H.264 sink: the encoder is configured and idle.
    assert state.streamer.h264.online is False
    assert state.streamer.sinks.h264.has_clients is False
    assert state.params.quality == 80
    assert state.limits.desired_fps.max == 70
    # The capture device cannot switch resolution, so kvmd omits both.
    assert state.params.resolution is None
    assert state.limits.available_resolutions is None


async def test_get_state_with_an_h264_client(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The one thing the capture cannot show, since it asks for video without
    # consuming it: a viewer on the H.264 sink brings the encoder online.
    body = _state()
    streamer = body["result"]["streamer"]
    streamer["h264"] |= {"online": True, "fps": 20}
    streamer["sinks"]["h264"]["has_clients"] = True
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.streamer is not None
    assert state.streamer.h264 is not None
    assert state.streamer.h264.online is True
    assert state.streamer.h264.fps == 20
    assert state.streamer.sinks.h264.has_clients is True


async def test_get_state_applied(mock_api: respx.MockRouter, client: PiKVM) -> None:
    # `applied` is what the running streamer ended up with, and the only way
    # to tell whether a set_params() call took effect.
    mock_api.get("/api/streamer").mock(
        return_value=httpx.Response(200, json=load_json("streamer"))
    )
    state = await client.streamer.get_state()
    assert state.applied.desired_fps == 20
    assert state.applied.quality == 80


async def test_get_state_off(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer").mock(
        return_value=httpx.Response(200, json=_state(streamer=None))
    )
    state = await client.streamer.get_state()
    assert state.streamer is None
    assert state.params.quality == 80


async def test_get_state_without_h264(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd builds params and limits conditionally: no H.264 configured means
    # no h264_bitrate/h264_gop anywhere, and ustreamer omits its h264 block.
    body = _state()
    result = body["result"]
    result["features"]["h264"] = False
    for block in ("params", "applied"):
        result[block].pop("h264_bitrate")
        result[block].pop("h264_gop")
    result["limits"].pop("h264_bitrate")
    result["limits"].pop("h264_gop")
    result["streamer"].pop("h264")
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.params.h264_bitrate is None
    assert state.limits.h264_gop is None
    assert state.streamer is not None
    assert state.streamer.h264 is None


async def test_get_state_with_resolution(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    body = _state()
    result = body["result"]
    result["features"]["resolution"] = True
    result["params"]["resolution"] = "1920x1080"
    result["applied"]["resolution"] = "1920x1080"
    result["limits"]["available_resolutions"] = ["1280x720", "1920x1080"]
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.params.resolution == "1920x1080"
    assert state.limits.available_resolutions == ["1280x720", "1920x1080"]


async def test_get_state_source_offline(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    body = _state()
    body["result"]["streamer"]["source"] |= {"captured_fps": 0, "online": False}
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.streamer is not None
    assert state.streamer.source.online is False
    assert state.streamer.source.captured_fps == 0


async def test_snapshot(mock_api: respx.MockRouter, client: PiKVM) -> None:
    jpeg_data = b"\xff\xd8\xff\xe0fake-jpeg"
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200, content=jpeg_data, headers={"Content-Type": "image/jpeg"}
        )
    )
    image = await client.streamer.snapshot()
    assert image.data == jpeg_data
    assert image.online is None
    assert "allow_offline" not in str(mock_api.calls[-1].request.url)


async def test_snapshot_allow_offline(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    jpeg_data = b"\xff\xd8\xff\xe0placeholder"
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200, content=jpeg_data, headers={"Content-Type": "image/jpeg"}
        )
    )
    image = await client.streamer.snapshot(allow_offline=True)
    assert image.data == jpeg_data
    assert "allow_offline=1" in str(mock_api.calls[-1].request.url)


async def test_get_ocr_info(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/ocr").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "ocr": {
                        "enabled": True,
                        "langs": {
                            "available": ["eng", "osd", "rus"],
                            "default": ["eng"],
                        },
                    }
                },
            },
        )
    )
    info = await client.streamer.get_ocr_info()
    assert info.enabled is True
    assert info.langs.available == ["eng", "osd", "rus"]
    assert info.langs.default == ["eng"]


async def test_ocr(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200,
            content=b"Hello World",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
    )
    text = await client.streamer.ocr()
    assert text == "Hello World"
    request = mock_api.calls[-1].request
    assert "ocr=1" in str(request.url)
    assert "ocr_langs" not in str(request.url)


async def test_ocr_with_langs(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200,
            content=b"Hello",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
    )
    await client.streamer.ocr(langs=["eng", "rus"])
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "ocr=1" in url
    # kvmd splits on comma; '+' would be sent as %2B and treated as one token
    assert "ocr_langs=eng%2Crus" in url or "ocr_langs=eng,rus" in url


async def test_ocr_allow_offline(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200,
            content=b"NO LIVE VIDEO",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
    )
    text = await client.streamer.ocr(allow_offline=True)
    assert text == "NO LIVE VIDEO"
    url = str(mock_api.calls[-1].request.url)
    assert "ocr=1" in url
    assert "allow_offline=1" in url


async def test_delete_snapshot(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.delete("/api/streamer/snapshot").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.streamer.delete_snapshot()


async def test_snapshot_reads_the_ustreamer_headers(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # These headers are the only way to tell a real frame from the
    # "NO LIVE VIDEO" placeholder kvmd serves when the source is offline.
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200,
            content=b"\xff\xd8\xff\xe0jpeg",
            headers={
                "Content-Type": "image/jpeg",
                "X-UStreamer-Online": "true",
                "X-UStreamer-Width": "1920",
                "X-UStreamer-Height": "1080",
                "X-Timestamp": "1786870260.36",
            },
        )
    )
    image = await client.streamer.snapshot()
    assert image.online is True
    assert image.width == 1920
    assert image.height == 1080
    assert image.timestamp == 1786870260.36


async def test_snapshot_offline_frame(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200,
            content=b"placeholder",
            headers={"X-UStreamer-Online": "false"},
        )
    )
    image = await client.streamer.snapshot(allow_offline=True)
    assert image.online is False


async def test_snapshot_save_and_load(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(200, content=b"jpeg")
    )
    await client.streamer.snapshot(save=True)
    assert mock_api.calls[-1].request.url.params["save"] == "1"
    await client.streamer.snapshot(load=True)
    assert mock_api.calls[-1].request.url.params["load"] == "1"


async def test_snapshot_preview(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(200, content=b"jpeg")
    )
    await client.streamer.snapshot(
        preview=True, preview_max_width=640, preview_max_height=480, preview_quality=50
    )
    params = mock_api.calls[-1].request.url.params
    assert params["preview"] == "1"
    assert params["preview_max_width"] == "640"
    assert params["preview_max_height"] == "480"
    assert params["preview_quality"] == "50"


async def test_ocr_with_crop(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200, content=b"cropped", headers={"Content-Type": "text/plain"}
        )
    )
    await client.streamer.ocr(left=10, top=20, right=300, bottom=400)
    params = mock_api.calls[-1].request.url.params
    assert params["ocr_left"] == "10"
    assert params["ocr_top"] == "20"
    assert params["ocr_right"] == "300"
    assert params["ocr_bottom"] == "400"


async def test_set_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/streamer/set_params").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.streamer.set_params(quality=70, desired_fps=25)
    params = mock_api.calls[-1].request.url.params
    assert params["quality"] == "70"
    assert params["desired_fps"] == "25"
    assert "resolution" not in params


async def test_set_params_resolution(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/streamer/set_params").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.streamer.set_params(resolution="1280x720", h264_gop=30)
    params = mock_api.calls[-1].request.url.params
    assert params["resolution"] == "1280x720"
    assert params["h264_gop"] == "30"


async def test_set_params_unsupported(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/streamer/set_params").mock(
        return_value=httpx.Response(
            400,
            json={
                "ok": False,
                "result": {
                    "error": "StreamerH264NotSupported",
                    "error_msg": "H264 is not supported",
                },
            },
        )
    )
    with pytest.raises(APIError, match="H264 is not supported") as info:
        await client.streamer.set_params(h264_bitrate=5000)
    assert info.value.error == "StreamerH264NotSupported"


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/streamer/reset").mock(return_value=httpx.Response(200, json=OK))
    await client.streamer.reset()


async def test_get_state_without_quality(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # A capture path with no adjustable encoder: kvmd omits quality from both
    # params and applied.
    body = _state()
    result = body["result"]
    result["features"]["quality"] = False
    result["params"].pop("quality")
    result["applied"].pop("quality")
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.params.quality is None
    assert state.applied.quality is None
    assert state.features.quality is False


async def test_get_state_applied_differs_from_params(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd builds `applied` from the running streamer, so it lags behind a
    # change that has not taken effect yet.
    body = _state()
    body["result"]["params"]["desired_fps"] = 60
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.params.desired_fps == 60
    assert state.applied.desired_fps == 20


async def test_get_state_saved_snapshot(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    body = _state()
    body["result"]["snapshot"]["saved"] = {
        "online": True,
        "width": 1920,
        "height": 1080,
    }
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=body))
    state = await client.streamer.get_state()
    assert state.snapshot.saved is not None
    assert state.snapshot.saved.online is True
    assert state.snapshot.saved.width == 1920


async def test_snapshot_ignores_unparsable_headers(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # No capture pins these headers down, so a value we cannot read must not
    # cost the caller a perfectly good JPEG.
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200,
            content=b"jpeg",
            headers={"X-UStreamer-Width": "wide", "X-Timestamp": "just now"},
        )
    )
    image = await client.streamer.snapshot()
    assert image.data == b"jpeg"
    assert image.width is None
    assert image.timestamp is None


async def test_set_params_without_arguments(client: PiKVM) -> None:
    with pytest.raises(ConfigurationError, match="at least one parameter"):
        await client.streamer.set_params()


# --- ustreamer's own endpoints -------------------------------------------


def stream_step(name: str) -> dict[str, Any]:
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


def multipart(
    name: str, rename: Callable[[str], str] = lambda header: header
) -> tuple[bytes, str]:
    """Rebuild a recorded MJPEG stream.

    The scenario stores each part's headers, its length and its first four
    bytes, and no more: a JPEG off this device is a picture of the attached
    host's screen. The framing is what a parser reads, and that is recorded
    in full — only the picture inside each part is stood in for.

    Args:
        name: Step name from the ``media_stream`` scenario.
        rename: Applied to each header name on the way out, for replaying a
            recorded stream as something between the client and ustreamer
            would have rewritten it.

    Returns:
        The body and the ``Content-Type`` it arrived with.
    """
    recorded = stream_step(name)
    content_type = recorded["content_type"]
    boundary = content_type.partition("boundary=")[2]
    body = b""
    for part in recorded["parts"]:
        head = "".join(f"{rename(n)}: {v}\r\n" for n, v in part["headers"].items())
        head_bytes = bytes.fromhex(part["data_head"])
        body += f"--{boundary}\r\n{head}\r\n".encode()
        body += head_bytes + bytes(part["data_len"] - len(head_bytes))
        body += b"\r\n"
    # A stream has no end: the next part's boundary is already on the wire.
    body += f"--{boundary}\r\n".encode()
    return (body, content_type)


def streaming(name: str) -> httpx.Response:
    """Build the response a recorded MJPEG stream arrived as."""
    (body, content_type) = multipart(name)
    return httpx.Response(200, content=body, headers={"Content-Type": content_type})


async def test_get_ustreamer_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/streamer/state").mock(
        return_value=httpx.Response(200, json=stream_step("state_idle")["response"])
    )
    state = await client.streamer.get_ustreamer_state()
    # The same object kvmd relays into StreamerState.streamer, read from
    # ustreamer rather than from kvmd's poll of it.
    assert state.source.resolution.width == 1920
    assert state.encoder.type == "M2M-IMAGE"
    assert state.stream.clients == 0
    assert state.stream.clients_stat == {}


async def test_get_ustreamer_state_names_its_clients(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/streamer/state").mock(
        return_value=httpx.Response(
            200, json=stream_step("state_with_client")["response"]
        )
    )
    state = await client.streamer.get_ustreamer_state()
    assert state.stream.clients == 1
    ((_, stat),) = state.stream.clients_stat.items()
    # The id is ustreamer's; `key` is the only way a client finds its own row.
    assert stat.key == "aiopikvm-capture"
    assert stat.extra_headers is True
    assert stat.advance_headers is False
    assert stat.fps == 1


async def test_get_ustreamer_state_with_the_streamer_stopped(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # nginx has no upstream socket to reach, so this is a 502 and an HTML
    # page — not kvmd's 503, and not an envelope with an error to match on.
    recorded = stream_step("state_stopped")
    mock_api.get("/streamer/state").mock(
        return_value=httpx.Response(
            recorded["status"],
            text=recorded["body_excerpt"],
            headers={"Content-Type": recorded["content_type"]},
        )
    )
    with pytest.raises(APIError) as caught:
        await client.streamer.get_ustreamer_state()
    assert caught.value.status_code == 502
    assert not isinstance(caught.value, UnavailableError)
    assert caught.value.error == ""


async def test_mjpeg_yields_the_recorded_frames(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    recorded = stream_step("stream_plain")["parts"]
    mock_api.get("/streamer/stream").mock(return_value=streaming("stream_plain"))
    frames = [frame async for frame in client.streamer.mjpeg()]
    assert len(frames) == len(recorded)
    for frame, part in zip(frames, recorded, strict=True):
        assert len(frame.data) == part["data_len"]
        assert frame.data[:2] == b"\xff\xd8"
        assert frame.timestamp == float(part["headers"]["X-Timestamp"])
        assert frame.headers["Content-Type"] == "image/jpeg"
        # Without extra_headers ustreamer annotates nothing.
        assert frame.online is None
        assert frame.width is None


async def test_mjpeg_reads_the_extra_headers(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    part = stream_step("stream_extra_headers")["parts"][0]
    route = mock_api.get("/streamer/stream").mock(
        return_value=streaming("stream_extra_headers")
    )
    frames = [frame async for frame in client.streamer.mjpeg(extra_headers=True)]
    assert route.calls.last.request.url.params["extra_headers"] == "1"
    first = frames[0]
    assert first.online is True
    assert first.width == 1920
    assert first.height == 1080
    assert first.dropped == int(part["headers"]["X-UStreamer-Dropped"])
    assert first.client_fps == int(part["headers"]["X-UStreamer-Client-FPS"])
    assert first.latency == float(part["headers"]["X-UStreamer-Latency"])
    # The timing headers have no field of their own; the raw headers are how
    # a caller reaches them.
    assert (
        first.headers["X-UStreamer-Grab-Begin-Time"]
        == (part["headers"]["X-UStreamer-Grab-Begin-Time"])
    )


async def test_mjpeg_zero_data_keeps_the_headers(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    route = mock_api.get("/streamer/stream").mock(
        return_value=streaming("stream_zero_data")
    )
    # One byte at a time: every part header and every boundary lands split
    # across chunks, which is the case a buffering parser gets wrong.
    frames = [
        frame
        async for frame in client.streamer.mjpeg(
            zero_data=True, extra_headers=True, chunk_size=1
        )
    ]
    assert route.calls.last.request.url.params["zero_data"] == "1"
    assert len(frames) == 2
    assert all(frame.data == b"" for frame in frames)
    assert frames[0].online is True
    assert frames[0].width == 1920


async def test_mjpeg_names_the_connection(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    route = mock_api.get("/streamer/stream").mock(
        return_value=streaming("stream_plain")
    )
    async for _ in client.streamer.mjpeg(key="watcher"):
        break
    assert route.calls.last.request.url.params["key"] == "watcher"


async def test_mjpeg_sends_no_flags_by_default(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    route = mock_api.get("/streamer/stream").mock(
        return_value=streaming("stream_plain")
    )
    async for _ in client.streamer.mjpeg():
        break
    assert route.calls.last.request.url.query == b""


async def test_mjpeg_reads_part_headers_whatever_their_case(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The recorded stream replayed with every header name lower-cased.

    ustreamer sends canonical casing and HTTP does not promise it survives a
    hop, so a rewriting proxy is the one thing that changes it — and that is
    exactly what the "no Content-Length" message below exists to describe.
    Matching the name case-sensitively made the parser blame it for the
    header being absent when it was there all along.
    """
    (body, content_type) = multipart("stream_extra_headers", rename=str.lower)
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Type": content_type}
        )
    )
    frames = [frame async for frame in client.streamer.mjpeg(extra_headers=True)]
    assert frames
    assert frames[0].online is True
    assert frames[0].width == 1920
    # Read without regard to case, handed over in the case they arrived in.
    assert "content-length" in frames[0].headers


async def test_mjpeg_without_a_content_length(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # What advance_headers puts on the wire, recorded verbatim: ustreamer
    # sends the headers before it has the frame, so it cannot say how long
    # one is. The client does not ask for that flag, but a proxy that
    # rewrites the stream would look the same.
    recorded = stream_step("stream_advance_headers")
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200,
            content=recorded["raw"].encode("latin-1"),
            headers={"Content-Type": recorded["content_type"]},
        )
    )
    with pytest.raises(ResponseError, match="no Content-Length"):
        [frame async for frame in client.streamer.mjpeg()]


async def test_mjpeg_with_an_unreadable_content_length(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    body = b"--x\r\nContent-Type: image/jpeg\r\nContent-Length: soon\r\n\r\n"
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "multipart/x-mixed-replace;boundary=x"},
        )
    )
    with pytest.raises(ResponseError, match="which is not a length"):
        [frame async for frame in client.streamer.mjpeg()]


async def test_mjpeg_of_something_that_is_not_a_stream(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(200, text="<html>hello</html>")
    )
    with pytest.raises(ResponseError, match="rather than a multipart stream"):
        [frame async for frame in client.streamer.mjpeg()]


async def test_mjpeg_falls_back_to_the_ustreamer_boundary(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    (body, _) = multipart("stream_plain")
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200, content=body, headers={"Content-Type": "multipart/x-mixed-replace"}
        )
    )
    frames = [frame async for frame in client.streamer.mjpeg()]
    assert len(frames) == 2


async def test_mjpeg_ignores_unparsable_headers(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    body = (
        b"--x\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n"
        b"X-Timestamp: just now\r\nX-UStreamer-Width: wide\r\n\r\njpeg\r\n--x\r\n"
    )
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "multipart/x-mixed-replace;boundary=x"},
        )
    )
    frames = [frame async for frame in client.streamer.mjpeg()]
    assert frames[0].data == b"jpeg"
    assert frames[0].timestamp is None
    assert frames[0].width is None


@pytest.mark.parametrize("chunk_size", [65536, 8])
async def test_mjpeg_drops_what_the_close_delimiter_ends(
    mock_api: respx.MockRouter, client: PiKVM, chunk_size: int
) -> None:
    """The close delimiter ends the stream, wherever the reads fall.

    `multipart()` leaves the next part's boundary on the wire, the way a
    stream that never ends does. Closing it means `--<boundary>--`, which is
    what RFC 2046 calls the close delimiter and what the reader looks for —
    appending `--` after the whole line, as this test used to, is a boundary
    followed by rubbish, and the reader stops on that for the ordinary reason
    that the body ran out. A whole part after the delimiter is what tells the
    two apart: it is dropped here, and read as a third frame if the branch
    goes (#144).

    The two chunk sizes are the same bytes cut differently. At the default
    the delimiter and the epilogue reach `feed()` together and emptying the
    buffer was enough; at eight they arrive in separate reads, and the reader
    used to parse the epilogue like any other part — so whether RFC 2046
    §5.1.1 was honoured came down to where a socket read happened to fall
    (#176).
    """
    (body, content_type) = multipart("stream_plain")
    boundary = content_type.partition("boundary=")[2].encode()
    closed = body.removesuffix(b"\r\n") + b"--\r\n"
    epilogue = (
        b"--"
        + boundary
        + b"\r\nContent-Type: image/jpeg\r\nContent-Length: 4\r\n\r\nJUNK\r\n"
    )
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200, content=closed + epilogue, headers={"Content-Type": content_type}
        )
    )
    # ustreamer never ends its stream, so this only turns up when something
    # else finished the body for it — and it is not a missing Content-Length.
    frames = [frame async for frame in client.streamer.mjpeg(chunk_size=chunk_size)]
    assert [frame.data[:4] for frame in frames] == [b"\xff\xd8\xff\xe1"] * 2


async def test_the_safari_workaround_stream_still_parses(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """`dual_final_frames=1` is a flag this client does not offer (#144).

    The recorded step says it "parses fine", which was a claim nothing ran:
    ustreamer keeps `Content-Length` under it and only repeats the last part
    of a series, so the framing is ordinary and the reader has no reason to
    care. Recorded whole rather than part by part, so the body is the bytes
    as they came off the socket — the header is not: this is the one stream
    step recorded without its `Content-Type`, so the fake borrows the one
    every sibling step recorded, boundary included.
    """
    recorded = stream_step("stream_dual_final_frames")
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            200,
            content=str(recorded["raw"]).encode(),
            headers={"Content-Type": str(stream_step("stream_plain")["content_type"])},
        )
    )
    frames = [frame async for frame in client.streamer.mjpeg()]
    assert len(frames) == 1
    # `zero_data=1` was on beside it, which is what makes the part empty.
    assert frames[0].data == b""
    assert frames[0].headers["Content-Length"] == "0"


async def test_a_streamer_error_carries_no_kvmd_envelope(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Nothing under `/streamer` answers in kvmd's envelope (#144).

    ustreamer serves its own HTML for a path it does not have, so an error
    from it has no `error` field to match on — unlike every kvmd endpoint,
    where a caller can branch on the name. The recorded body is ustreamer's
    404; the path it was recorded at is not one this client requests, which
    is why the response is replayed at one that is.
    """
    recorded = stream_step("not_found")
    mock_api.get("/streamer/stream").mock(
        return_value=httpx.Response(
            recorded["status"],
            text=str(recorded["body_excerpt"]),
            headers={"Content-Type": recorded["content_type"]},
        )
    )
    with pytest.raises(APIError) as info:
        [frame async for frame in client.streamer.mjpeg()]
    assert info.value.status_code == 404
    assert info.value.error == ""
    assert info.value.error_msg == ""
