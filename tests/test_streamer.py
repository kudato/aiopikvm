"""StreamerResource tests."""

import httpx
import respx

from aiopikvm import PiKVM

STREAMER_STATE_RUNNING = {
    "ok": True,
    "result": {
        "features": {"h264": True, "quality": True, "resolution": False},
        "limits": {
            "desired_fps": {"max": 70, "min": 0},
            "h264_bitrate": {"max": 20000, "min": 25},
            "h264_gop": {"max": 60, "min": 0},
        },
        "params": {
            "desired_fps": 40,
            "h264_bitrate": 5000,
            "h264_gop": 30,
            "quality": 80,
        },
        "snapshot": {"saved": None},
        "streamer": {
            "encoder": {"quality": 80, "type": "M2M-IMAGE"},
            "h264": {"bitrate": 5000, "fps": 28, "gop": 30, "online": True},
            "instance_id": "",
            "sinks": {
                "h264": {"has_clients": True},
                "jpeg": {"has_clients": False},
            },
            "source": {
                "captured_fps": 56,
                "desired_fps": 40,
                "online": True,
                "resolution": {"height": 1080, "width": 1920},
            },
            "stream": {"clients": 0, "clients_stat": {}, "queued_fps": 0},
        },
    },
}

STREAMER_STATE_OFF = {
    "ok": True,
    "result": {
        "features": {"h264": True, "quality": True, "resolution": False},
        "limits": {
            "desired_fps": {"max": 70, "min": 0},
            "h264_bitrate": {"max": 20000, "min": 25},
            "h264_gop": {"max": 60, "min": 0},
        },
        "params": {
            "desired_fps": 40,
            "h264_bitrate": 5000,
            "h264_gop": 30,
            "quality": 80,
        },
        "snapshot": {"saved": None},
        "streamer": None,
    },
}


async def test_get_state_running(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer").mock(
        return_value=httpx.Response(200, json=STREAMER_STATE_RUNNING)
    )
    state = await client.streamer.get_state()
    assert state.streamer is not None
    assert state.streamer.source.online is True
    assert state.streamer.source.resolution.width == 1920
    assert state.streamer.source.resolution.height == 1080
    assert state.streamer.h264.online is True
    assert state.streamer.sinks.h264.has_clients is True
    assert state.params.quality == 80
    assert state.limits.desired_fps.max == 70


async def test_get_state_off(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer").mock(
        return_value=httpx.Response(200, json=STREAMER_STATE_OFF)
    )
    state = await client.streamer.get_state()
    assert state.streamer is None
    assert state.params.quality == 80


async def test_get_state_source_offline(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    payload = {
        "ok": True,
        "result": {
            **STREAMER_STATE_RUNNING["result"],  # type: ignore[dict-item]
            "streamer": {
                **STREAMER_STATE_RUNNING["result"]["streamer"],  # type: ignore[dict-item]
                "source": {
                    "captured_fps": 0,
                    "desired_fps": 40,
                    "online": False,
                    "resolution": {"height": 1080, "width": 1920},
                },
            },
        },
    }
    mock_api.get("/api/streamer").mock(return_value=httpx.Response(200, json=payload))
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
    data = await client.streamer.snapshot()
    assert data == jpeg_data


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


async def test_delete_snapshot(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.delete("/api/streamer/snapshot").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.streamer.delete_snapshot()
