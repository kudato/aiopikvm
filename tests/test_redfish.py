"""RedfishResource tests."""

import json

import httpx
import pytest
import respx

from aiopikvm import APIError, AuthError, PiKVM

RESET_PATH = "/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset"

SERVICE_ROOT = {
    "@odata.id": "/redfish/v1",
    "Name": "PiKVM Redfish Service",
    "RedfishVersion": "1.0.0",
}

SYSTEMS_COLLECTION = {
    "@odata.id": "/redfish/v1/Systems",
    "Members": [{"@odata.id": "/redfish/v1/Systems/0"}],
    "Members@odata.count": 1,
}

SYSTEM_DETAIL = {
    "@odata.id": "/redfish/v1/Systems/0",
    "Name": "Computer System",
    "PowerState": "On",
    "IndicatorLED": "Off",
}


async def test_get_root(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1").mock(
        return_value=httpx.Response(200, json=SERVICE_ROOT)
    )
    result = await client.redfish.get_root()
    assert result["Name"] == "PiKVM Redfish Service"


async def test_get_systems(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1/Systems").mock(
        return_value=httpx.Response(200, json=SYSTEMS_COLLECTION)
    )
    result = await client.redfish.get_systems()
    assert result["Members@odata.count"] == 1


async def test_get_system(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1/Systems/0").mock(
        return_value=httpx.Response(200, json=SYSTEM_DETAIL)
    )
    result = await client.redfish.get_system()
    assert result["PowerState"] == "On"


async def test_get_system_custom_id(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1/Systems/1").mock(
        return_value=httpx.Response(200, json=SYSTEM_DETAIL)
    )
    result = await client.redfish.get_system(1)
    assert result["Name"] == "Computer System"


async def test_update_system(mock_api: respx.MockRouter, client: PiKVM) -> None:
    updated = {**SYSTEM_DETAIL, "IndicatorLED": "Lit"}
    mock_api.patch("/api/redfish/v1/Systems/0").mock(
        return_value=httpx.Response(200, json=updated)
    )
    result = await client.redfish.update_system(IndicatorLED="Lit")
    request = mock_api.calls[-1].request
    body = json.loads(request.content)
    assert body["IndicatorLED"] == "Lit"
    assert result["IndicatorLED"] == "Lit"


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    result = await client.redfish.reset("ForceRestart")
    assert result["status"] == "ok"


async def test_reset_default_type(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    await client.redfish.reset()
    request = mock_api.calls[-1].request
    body = json.loads(request.content)
    assert body["ResetType"] == "ForceRestart"


async def test_reset_auth_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await client.redfish.reset()


async def test_reset_api_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(APIError):
        await client.redfish.reset()
