"""SystemResource tests."""

import httpx
import respx

from aiopikvm import PiKVM

INFO_RESPONSE = {
    "ok": True,
    "result": {
        "hw": {
            "health": {"cpu": {"percent": 5}},
            "platform": {"base": "Raspberry Pi 4", "type": "rpi"},
        },
        "system": {"kvmd": {"version": "3.100"}},
    },
}

INFO_HW_ONLY = {
    "ok": True,
    "result": {
        "hw": {
            "health": {"cpu": {"percent": 5}},
            "platform": {"base": "Raspberry Pi 4", "type": "rpi"},
        },
    },
}

LOG_TEXT = (
    "[2025-06-10 22:38:07 kvmd.service] --- kvmd.apps.kvmd INFO --- Started\n"
    "[2025-06-10 22:38:15 kvmd.service] --- kvmd.apps.kvmd INFO --- Ready\n"
)


async def test_get_info(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/info").mock(return_value=httpx.Response(200, json=INFO_RESPONSE))
    result = await client.system.get_info()
    assert "hw" in result
    assert "system" in result
    assert result["hw"]["platform"]["type"] == "rpi"


async def test_get_info_with_fields(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/info").mock(return_value=httpx.Response(200, json=INFO_HW_ONLY))
    result = await client.system.get_info("hw")
    request = mock_api.calls[-1].request
    assert "fields=hw" in str(request.url)
    assert "hw" in result


async def test_get_info_multiple_fields(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/info").mock(return_value=httpx.Response(200, json=INFO_RESPONSE))
    result = await client.system.get_info("hw", "system")
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "fields=hw" in url
    assert "fields=system" in url
    assert "hw" in result
    assert "system" in result


async def test_get_log(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/log").mock(return_value=httpx.Response(200, text=LOG_TEXT))
    log = await client.system.get_log()
    assert "kvmd.apps.kvmd" in log
    assert "Started" in log


async def test_get_log_with_seek(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/log").mock(return_value=httpx.Response(200, text=LOG_TEXT))
    log = await client.system.get_log(seek=3600)
    request = mock_api.calls[-1].request
    assert "seek=3600" in str(request.url)
    assert len(log) > 0
