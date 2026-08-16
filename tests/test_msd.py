"""MSDResource tests."""

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from aiopikvm import AuthError, PiKVM, UnavailableError

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


async def test_set_params_image(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(image="boot.iso")
    assert mock_api.calls[-1].request.url.params["image"] == "boot.iso"


async def test_set_params_ejects_with_empty_image(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd reads an empty `image` as "eject", which is why the parameter is
    # sent whenever it is not None rather than whenever it is truthy.
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(image="")
    assert mock_api.calls[-1].request.url.params["image"] == ""


async def test_download(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd/read").mock(
        return_value=httpx.Response(200, content=b"iso-bytes")
    )
    chunks = [chunk async for chunk in client.msd.download("boot.iso")]
    assert b"".join(chunks) == b"iso-bytes"
    params = mock_api.calls[-1].request.url.params
    assert params["image"] == "boot.iso"
    assert "compress" not in params


async def test_download_compressed(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd/read").mock(
        return_value=httpx.Response(200, content=b"compressed")
    )
    chunks = [chunk async for chunk in client.msd.download("boot.iso", compress="zstd")]
    assert b"".join(chunks) == b"compressed"
    assert mock_api.calls[-1].request.url.params["compress"] == "zstd"


async def test_download_disables_the_read_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # An image transfer outlives the 10 s client default many times over.
    mock_api.get("/api/msd/read").mock(return_value=httpx.Response(200, content=b""))
    async for _ in client.msd.download("boot.iso"):
        pass  # pragma: no cover - the body is empty
    timeout = mock_api.calls[-1].request.extensions["timeout"]
    assert timeout["read"] is None
    assert timeout["connect"] == 10.0


async def test_download_error_status(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd/read").mock(
        return_value=httpx.Response(
            503,
            json={
                "ok": False,
                "result": {"error": "UnavailableError", "error_msg": "MSD is offline"},
            },
        )
    )
    with pytest.raises(UnavailableError, match="MSD is offline"):
        async for _ in client.msd.download("boot.iso"):
            pass  # pragma: no cover - the request fails before yielding


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
