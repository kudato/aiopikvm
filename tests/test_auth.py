"""AuthResource tests."""

import httpx
import pytest
import respx

from aiopikvm import AuthError, PiKVM


async def test_login(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/auth/login").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    result = await client.auth.login("admin", "admin")
    assert result == {}


async def test_login_with_totp(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/auth/login").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.auth.login("admin", "pass", totp="123456")

    request = mock_api.calls[-1].request
    import json

    body = json.loads(request.content)
    assert body["passwd"] == "pass123456"


async def test_check(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/auth/check").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    result = await client.auth.check()
    assert result == {}


async def test_logout(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/auth/logout").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    result = await client.auth.logout()
    assert result == {}


async def test_auth_error_401(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/auth/check").mock(
        return_value=httpx.Response(401, json={"ok": False})
    )
    with pytest.raises(AuthError) as exc_info:
        await client.auth.check()
    assert exc_info.value.status_code == 401


async def test_auth_error_403(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/auth/check").mock(
        return_value=httpx.Response(403, json={"ok": False})
    )
    with pytest.raises(AuthError) as exc_info:
        await client.auth.check()
    assert exc_info.value.status_code == 403
