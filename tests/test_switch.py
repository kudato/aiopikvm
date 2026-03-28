"""SwitchResource tests."""

import json

import httpx
import respx

from aiopikvm import PiKVM

SWITCH_STATE = {
    "ok": True,
    "result": {
        "active": "port0",
        "ports": {
            "port0": {"name": "Server 1"},
            "port1": {"name": "Server 2"},
        },
    },
}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/switch").mock(
        return_value=httpx.Response(200, json=SWITCH_STATE)
    )
    state = await client.switch.get_state()
    assert state.active == "port0"
    assert "port0" in state.ports
    assert state.ports["port0"].name == "Server 1"


async def test_set_active(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_active("port1")
    request = mock_api.calls[-1].request
    assert "port=port1" in str(request.url)


async def test_create_edid(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/edids/create").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.create_edid(
        "edid1", "00ffffffffffff00", description="Test EDID"
    )
    request = mock_api.calls[-1].request
    body = json.loads(request.content)
    assert body["id"] == "edid1"
    assert body["data"] == "00ffffffffffff00"
    assert body["description"] == "Test EDID"


async def test_change_edid(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/edids/change").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.change_edid("port0", "edid1")


async def test_get_edids(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/switch/edids").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "edids": [
                        {"id": "default", "data": "00ff...", "description": "Default"},
                    ]
                },
            },
        )
    )
    edids = await client.switch.get_edids()
    assert len(edids) == 1
    assert edids[0].id == "default"


async def test_remove_edid(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/edids/remove").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.remove_edid("edid1")


async def test_set_active_prev(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active_prev").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_active_prev()


async def test_set_active_next(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active_next").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_active_next()


async def test_set_beacon(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_beacon").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_beacon(True, port=3)
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "state=1" in url
    assert "port=3" in url


async def test_set_beacon_minimal(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_beacon").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_beacon(False)
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "state=0" in url
    assert "port" not in url


async def test_set_port_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_port_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_port_params(0, name="Server1", dummy=True)
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "port=0" in url
    assert "name=Server1" in url
    assert "dummy=1" in url


async def test_set_port_params_minimal(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/switch/set_port_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_port_params(2)
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "port=2" in url
    assert "name" not in url


async def test_set_colors(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_colors").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.set_colors("FFA500:BF:0028")
    request = mock_api.calls[-1].request
    assert "beacon=FFA500" in str(request.url)


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/reset").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.reset(0)
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "unit=0" in url
    assert "bootloader" not in url


async def test_reset_with_bootloader(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/reset").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.reset(1, bootloader=True)
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "unit=1" in url
    assert "bootloader=1" in url


async def test_atx_power(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/atx/power").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.atx_power(0, "on")
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "port=0" in url
    assert "action=on" in url


async def test_atx_click(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.switch.atx_click(3, "power")
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "port=3" in url
    assert "button=power" in url
