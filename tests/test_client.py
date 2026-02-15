"""PiKVM client lifecycle tests."""

import httpx
import pytest
import respx

from aiopikvm import PiKVM, PiKVMError
from aiopikvm._base_resource import BaseResource


async def test_context_manager(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as client:
        assert client._client is not None
        assert client.atx is not None
        assert client.hid is not None
        assert client.msd is not None
        assert client.gpio is not None
        assert client.streamer is not None
        assert client.switch is not None
        assert client.redfish is not None
        assert client.prometheus is not None
        assert client.auth is not None


async def test_auth_headers(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="secret") as client:
        assert client._client is not None
        assert client._client.headers["X-KVMD-User"] == "admin"
        assert client._client.headers["X-KVMD-Passwd"] == "secret"


async def test_totp_concat(mock_api: respx.MockRouter) -> None:
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="secret", totp="123456"
    ) as client:
        assert client._client is not None
        assert client._client.headers["X-KVMD-Passwd"] == "secret123456"


async def test_ws_factory(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as client:
        ws = client.ws()
        assert ws._url == "wss://pikvm.local/api/ws?stream=0"


async def test_client_close(mock_api: respx.MockRouter) -> None:
    client = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    async with client:
        assert client._client is not None
    assert client._client is None


def test_access_before_aenter() -> None:
    client = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    for name in (
        "auth",
        "atx",
        "hid",
        "msd",
        "gpio",
        "streamer",
        "switch",
        "redfish",
        "prometheus",
    ):
        with pytest.raises(PiKVMError, match="async context"):
            getattr(client, name)


def test_access_unknown_attr() -> None:
    client = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    with pytest.raises(AttributeError):
        client.nonexistent  # noqa: B018


async def test_external_http_client_not_closed(mock_api: respx.MockRouter) -> None:
    ext_client = httpx.AsyncClient(
        base_url="https://pikvm.local",
        headers={"X-KVMD-User": "admin", "X-KVMD-Passwd": "admin"},
    )
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="admin", http_client=ext_client
    ):
        pass
    assert not ext_client.is_closed
    await ext_client.aclose()


async def test_resources_cleared_after_aexit(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        _ = kvm.atx
        _ = kvm.hid
        assert "atx" in kvm.__dict__
        assert "hid" in kvm.__dict__
    assert "atx" not in kvm.__dict__
    assert "hid" not in kvm.__dict__


async def test_explicit_aclose(mock_api: respx.MockRouter) -> None:
    kvm = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    await kvm.__aenter__()
    _ = kvm.atx
    assert kvm._client is not None
    assert "atx" in kvm.__dict__
    await kvm.aclose()
    assert kvm._client is None
    assert "atx" not in kvm.__dict__


async def test_ws_factory_uses_timeout(mock_api: respx.MockRouter) -> None:
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="admin", timeout=5.0
    ) as kvm:
        ws = kvm.ws()
        assert ws._open_timeout == 5.0
        assert ws._close_timeout == 5.0

        ws2 = kvm.ws(open_timeout=2.0, close_timeout=3.0)
        assert ws2._open_timeout == 2.0
        assert ws2._close_timeout == 3.0


async def test_patch_request(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.patch("/api/test").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"patched": True}})
    )
    resource = BaseResource(client)
    result = await resource._patch("/api/test", json={"key": "value"})
    assert result == {"patched": True}
