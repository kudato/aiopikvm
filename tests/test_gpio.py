"""GPIOResource tests."""

import httpx
import respx

from aiopikvm import PiKVM

GPIO_STATE = {
    "ok": True,
    "result": {
        "inputs": {"input0": {"online": True, "state": False}},
        "outputs": {"relay0": {"online": True, "state": False, "busy": False}},
    },
}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/gpio").mock(return_value=httpx.Response(200, json=GPIO_STATE))
    state = await client.gpio.get_state()
    assert "input0" in state.inputs
    assert "relay0" in state.outputs
    assert state.inputs["input0"].state is False


async def test_switch(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/switch").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.gpio.switch("relay0", True)
    request = mock_api.calls[-1].request
    assert "channel=relay0" in str(request.url)
    assert "state=1" in str(request.url)


async def test_pulse(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/pulse").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.gpio.pulse("relay0", delay=0.5)
    request = mock_api.calls[-1].request
    assert "channel=relay0" in str(request.url)
    assert "delay=0.5" in str(request.url)


async def test_pulse_no_delay(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/pulse").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.gpio.pulse("relay0")
    request = mock_api.calls[-1].request
    assert "channel=relay0" in str(request.url)
    assert "delay" not in str(request.url)
