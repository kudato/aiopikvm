"""ATXResource tests."""

import httpx
import pytest
import respx

from aiopikvm import APIError, PiKVM

ATX_STATE = {
    "ok": True,
    "result": {
        "enabled": True,
        "busy": False,
        "leds": {"power": True, "hdd": False},
    },
}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=ATX_STATE))
    state = await client.atx.get_state()
    assert state.enabled is True
    assert state.busy is False
    assert state.leds.power is True
    assert state.leds.hdd is False


async def test_power_on(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.power_on()
    request = mock_api.calls[-1].request
    assert "action=on" in str(request.url)


async def test_power_off(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.power_off()
    request = mock_api.calls[-1].request
    assert "action=off" in str(request.url)


async def test_power_off_hard(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.power_off_hard()
    request = mock_api.calls[-1].request
    assert "action=off_hard" in str(request.url)


async def test_reset_hard(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.reset_hard()
    request = mock_api.calls[-1].request
    assert "action=reset_hard" in str(request.url)


async def test_click_power(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.click_power()
    request = mock_api.calls[-1].request
    assert "button=power" in str(request.url)


async def test_click_power_long(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.click_power_long()
    request = mock_api.calls[-1].request
    assert "button=power_long" in str(request.url)


async def test_click_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.click_reset()
    request = mock_api.calls[-1].request
    assert "button=reset" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_power_on_wait_param(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.power_on(wait=wait)
    request = mock_api.calls[-1].request
    assert f"wait={int(wait)}" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_click_power_wait_param(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.click_power(wait=wait)
    request = mock_api.calls[-1].request
    assert f"wait={int(wait)}" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_click_reset_wait_param(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.click_reset(wait=wait)
    request = mock_api.calls[-1].request
    assert f"wait={int(wait)}" in str(request.url)


async def test_api_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            200, json={"ok": False, "result": {"error": "ATX disabled"}}
        )
    )
    with pytest.raises(APIError, match="ATX disabled"):
        await client.atx.get_state()
