"""MSDResource tests."""

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from aiopikvm import AuthError, PiKVM

MSD_STATE = {
    "ok": True,
    "result": {
        "enabled": True,
        "online": True,
        "busy": False,
        "drive": {"image": None, "connected": False, "cdrom": False},
        "storage": {"size": 1073741824, "free": 536870912, "images": {}},
    },
}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd").mock(return_value=httpx.Response(200, json=MSD_STATE))
    state = await client.msd.get_state()
    assert state.enabled is True
    assert state.storage.size == 1073741824


async def test_upload_bytes(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/write").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.upload("test.iso", b"fake-iso-data")
    request = mock_api.calls[-1].request
    assert "image=test.iso" in str(request.url)


async def test_upload_remote(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.upload_remote("https://example.com/image.iso")


async def test_set_connected(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_connected").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_connected(True)


async def test_remove(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/remove").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.remove("test.iso")


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/reset").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.reset()


async def test_set_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(cdrom=True, rw=False)
    request = mock_api.calls[-1].request
    assert "cdrom=1" in str(request.url)
    assert "rw=0" in str(request.url)


async def test_set_params_partial(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(cdrom=True)
    request = mock_api.calls[-1].request
    assert "cdrom=1" in str(request.url)
    assert "rw" not in str(request.url)


async def test_upload_streaming(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/write").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )

    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"
        yield b"chunk2"

    await client.msd.upload("test.iso", data_gen())
    request = mock_api.calls[-1].request
    assert "image=test.iso" in str(request.url)


async def test_upload_remote_with_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.upload_remote("https://example.com/image.iso", timeout=30)
    request = mock_api.calls[-1].request
    assert "timeout=30" in str(request.url)


async def test_upload_streaming_auth_error(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write").mock(return_value=httpx.Response(401))

    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"

    with pytest.raises(AuthError):
        await client.msd.upload("test.iso", data_gen())
