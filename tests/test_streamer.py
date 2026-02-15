"""StreamerResource tests."""

import httpx
import respx

from aiopikvm import PiKVM

STREAMER_STATE = {
    "ok": True,
    "result": {
        "enabled": True,
        "source": {"online": True, "resolution": {"width": 1920, "height": 1080}},
    },
}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer").mock(
        return_value=httpx.Response(200, json=STREAMER_STATE)
    )
    state = await client.streamer.get_state()
    assert state.enabled is True
    assert state.source.online is True
    assert state.source.resolution is not None
    assert state.source.resolution.width == 1920
    assert state.source.resolution.height == 1080


async def test_snapshot(mock_api: respx.MockRouter, client: PiKVM) -> None:
    jpeg_data = b"\xff\xd8\xff\xe0fake-jpeg"
    mock_api.get("/api/streamer/snapshot").mock(
        return_value=httpx.Response(
            200, content=jpeg_data, headers={"Content-Type": "image/jpeg"}
        )
    )
    data = await client.streamer.snapshot()
    assert data == jpeg_data


async def test_ocr(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/streamer/ocr").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"ocr": "Hello World"}}
        )
    )
    text = await client.streamer.ocr()
    assert text == "Hello World"


async def test_delete_snapshot(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.delete("/api/streamer/snapshot").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.streamer.delete_snapshot()
