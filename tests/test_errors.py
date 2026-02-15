"""Error handling tests — ConnectionTimeoutError, ConnectError."""

import httpx
import pytest
import respx

from aiopikvm import APIError, AuthError, ConnectError, ConnectionTimeoutError, PiKVM


async def test_connect_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(side_effect=httpx.ConnectError("Connection refused"))
    with pytest.raises(ConnectError, match="Connection refused"):
        await client.atx.get_state()


async def test_timeout_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(side_effect=httpx.ReadTimeout("Read timed out"))
    with pytest.raises(ConnectionTimeoutError, match="Read timed out"):
        await client.atx.get_state()


async def test_connect_timeout_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(side_effect=httpx.ConnectTimeout("Connect timed out"))
    with pytest.raises(ConnectionTimeoutError, match="Connect timed out"):
        await client.atx.get_state()


async def test_auth_error_post(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/reset").mock(return_value=httpx.Response(403))
    with pytest.raises(AuthError):
        await client.hid.reset()


async def test_timeout_on_raw_request(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/streamer/snapshot").mock(
        side_effect=httpx.ReadTimeout("Snapshot timed out")
    )
    with pytest.raises(ConnectionTimeoutError, match="Snapshot timed out"):
        await client.streamer.snapshot()


async def test_timeout_on_redfish(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset").mock(
        side_effect=httpx.ReadTimeout("Redfish timed out")
    )
    with pytest.raises(ConnectionTimeoutError, match="Redfish timed out"):
        await client.redfish.reset()


async def test_connect_error_on_redfish(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    with pytest.raises(ConnectError, match="Connection refused"):
        await client.redfish.reset()


async def test_invalid_json_response(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, text="not json at all")
    )
    with pytest.raises(APIError, match="Invalid JSON response"):
        await client.atx.get_state()


async def test_api_error_non_dict_result(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            200, json={"ok": False, "result": "some error string"}
        )
    )
    with pytest.raises(APIError, match="some error string"):
        await client.atx.get_state()


async def test_invalid_json_redfish(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    with pytest.raises(APIError, match="Invalid JSON response"):
        await client.redfish.reset()
