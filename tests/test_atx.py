"""ATXResource tests."""

import httpx
import pytest
import respx

from aiopikvm import APIError, BusyError, PiKVM
from tests.fixtures import load_json

OK = {"ok": True, "result": {}}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json=load_json("atx"))
    )
    state = await client.atx.get_state()
    # ATX is disabled on the capture device.
    assert state.enabled is False
    assert state.busy is False
    assert state.leds.power is False
    assert state.leds.hdd is False
    # kvmd guards the power and reset lines separately; busy is their union.
    assert state.acts.power is False
    assert state.acts.reset is False


async def test_power_on(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.power_on()
    request = mock_api.calls[-1].request
    assert "action=on" in str(request.url)


async def test_power_off(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.power_off()
    request = mock_api.calls[-1].request
    assert "action=off" in str(request.url)


async def test_power_off_hard(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.power_off_hard()
    request = mock_api.calls[-1].request
    assert "action=off_hard" in str(request.url)


async def test_reset_hard(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.reset_hard()
    request = mock_api.calls[-1].request
    assert "action=reset_hard" in str(request.url)


async def test_click_power(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.click_power()
    request = mock_api.calls[-1].request
    assert "button=power" in str(request.url)


async def test_click_power_long(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.click_power_long()
    request = mock_api.calls[-1].request
    assert "button=power_long" in str(request.url)


async def test_click_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.click_reset()
    request = mock_api.calls[-1].request
    assert "button=reset" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_power_on_wait_param(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    mock_api.post("/api/atx/power").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.power_on(wait=wait)
    request = mock_api.calls[-1].request
    assert f"wait={int(wait)}" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_click_power_wait_param(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    mock_api.post("/api/atx/click").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.click_power(wait=wait)
    request = mock_api.calls[-1].request
    assert f"wait={int(wait)}" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_click_reset_wait_param(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    mock_api.post("/api/atx/click").mock(return_value=httpx.Response(200, json=OK))
    await client.atx.click_reset(wait=wait)
    request = mock_api.calls[-1].request
    assert f"wait={int(wait)}" in str(request.url)


@pytest.mark.parametrize(
    ("call", "path"),
    [
        ("power_on", "/api/atx/power"),
        ("power_off", "/api/atx/power"),
        ("power_off_hard", "/api/atx/power"),
        ("reset_hard", "/api/atx/power"),
        ("click_power", "/api/atx/click"),
        ("click_power_long", "/api/atx/click"),
        ("click_reset", "/api/atx/click"),
    ],
)
async def test_wait_defaults_to_off(
    mock_api: respx.MockRouter, client: PiKVM, call: str, path: str
) -> None:
    # kvmd's own default is wait=false; holding the request for the length of
    # a long click ate 5.5 s of the 10 s client timeout.
    mock_api.post(path).mock(return_value=httpx.Response(200, json=OK))
    await getattr(client.atx, call)()
    assert mock_api.calls[-1].request.url.params["wait"] == "0"


async def test_disabled_plugin(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(
            400,
            json={
                "ok": False,
                "result": {
                    "error": "AtxDisabledError",
                    "error_msg": "ATX is disabled",
                },
            },
        )
    )
    with pytest.raises(APIError, match="ATX is disabled") as info:
        await client.atx.click_power()
    assert info.value.error == "AtxDisabledError"


async def test_busy(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(
            409,
            json={
                "ok": False,
                "result": {
                    "error": "AtxIsBusyError",
                    "error_msg": (
                        "Performing another ATX operation, please try again later"
                    ),
                },
            },
        )
    )
    with pytest.raises(BusyError, match="Performing another ATX operation") as info:
        await client.atx.power_on()
    assert info.value.error == "AtxIsBusyError"
