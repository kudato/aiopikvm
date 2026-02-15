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
