"""StreamerResource tests."""

import copy
from typing import Any

import httpx
import pytest
import respx

from aiopikvm import APIError, ConfigurationError, PiKVM
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
