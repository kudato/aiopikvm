"""RedfishResource tests."""

import httpx
import pytest
import respx

from aiopikvm import APIError, AuthError, PiKVM

RESET_PATH = "/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset"


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
    import json

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
