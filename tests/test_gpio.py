"""GPIOResource tests."""

import httpx
import pytest
import respx

from aiopikvm import PiKVM
from tests.fixtures import load_json

OK = {"ok": True, "result": {}}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/gpio").mock(
        return_value=httpx.Response(200, json=load_json("gpio"))
    )
    state = await client.gpio.get_state()
    channel = state.outputs["__v3_usb_breaker__"]
    assert channel.online is True
    assert channel.busy is False
    assert state.inputs == {}


async def test_get_state_exposes_the_scheme(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/gpio").mock(
        return_value=httpx.Response(200, json=load_json("gpio"))
    )
    state = await client.gpio.get_state()
    scheme = state.model.scheme.outputs["__v3_usb_breaker__"]
    assert scheme.switch is True
    assert scheme.hw.driver == "__gpio__"
    assert scheme.hw.pin == "5"
    # A zero delay means the channel cannot be pulsed at all.
    assert scheme.pulse.delay == 0.0


async def test_get_state_shortcuts_match_the_readings(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/gpio").mock(
        return_value=httpx.Response(200, json=load_json("gpio"))
    )
    state = await client.gpio.get_state()
    assert state.outputs is state.state.outputs
    assert state.inputs is state.state.inputs


async def test_get_state_keeps_the_view(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/gpio").mock(
        return_value=httpx.Response(200, json=load_json("gpio"))
    )
    state = await client.gpio.get_state()
    assert state.model.view.header.title[0]["text"] == "GPIO"
    assert state.model.view.table == []


async def test_switch(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/switch").mock(return_value=httpx.Response(200, json=OK))
    await client.gpio.switch("relay0", True)
    request = mock_api.calls[-1].request
    assert "channel=relay0" in str(request.url)
    assert "state=1" in str(request.url)


@pytest.mark.parametrize("wait", [True, False])
async def test_switch_wait(
    mock_api: respx.MockRouter, client: PiKVM, wait: bool
) -> None:
    # Without wait, kvmd answers as soon as the switch starts and anything
    # that fails afterwards only reaches its log.
    mock_api.post("/api/gpio/switch").mock(return_value=httpx.Response(200, json=OK))
    await client.gpio.switch("relay0", True, wait=wait)
    params = mock_api.calls[-1].request.url.params
    assert params.get("wait") == ("1" if wait else None)


async def test_switch_timeout(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/switch").mock(return_value=httpx.Response(200, json=OK))
    await client.gpio.switch("relay0", True, wait=True, timeout=45.0)
    assert mock_api.calls[-1].request.extensions["timeout"]["read"] == 45.0


async def test_pulse(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/pulse").mock(return_value=httpx.Response(200, json=OK))
    await client.gpio.pulse("relay0", delay=0.5)
    request = mock_api.calls[-1].request
    assert "channel=relay0" in str(request.url)
    assert "delay=0.5" in str(request.url)


async def test_pulse_no_delay(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/gpio/pulse").mock(return_value=httpx.Response(200, json=OK))
    await client.gpio.pulse("relay0")
    request = mock_api.calls[-1].request
    assert "channel=relay0" in str(request.url)
    assert "delay" not in str(request.url)
    assert "wait" not in request.url.params


async def test_pulse_wait_and_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/gpio/pulse").mock(return_value=httpx.Response(200, json=OK))
    await client.gpio.pulse("relay0", delay=30.0, wait=True, timeout=60.0)
    request = mock_api.calls[-1].request
    assert request.url.params["wait"] == "1"
    assert request.extensions["timeout"]["read"] == 60.0
