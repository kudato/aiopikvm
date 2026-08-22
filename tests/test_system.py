"""SystemResource tests.

The four `/api/info` payloads are real captures, one per shape kvmd can
answer with. They are what makes the legacy rearrangement visible: it is
not something the client does, and a hand-written payload would have shown
whatever the author believed it to be.
"""

import httpx
import pytest
import respx

from aiopikvm import AuthError, PiKVM
from tests.fixtures import load_json

LOG_TEXT = (
    "[2025-06-10 22:38:07 kvmd.service] --- kvmd.apps.kvmd INFO --- Started\n"
    "[2025-06-10 22:38:15 kvmd.service] --- kvmd.apps.kvmd INFO --- Ready\n"
)


async def test_get_info(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/info").mock(
        return_value=httpx.Response(200, json=load_json("info"))
    )
    result = await client.system.get_info()
    request = mock_api.calls[-1].request
    # kvmd's own default is the legacy shape, so a plain call sends nothing.
    assert "fields" not in request.url.params
    assert "legacy" not in request.url.params
    assert "hw" in result
    assert "system" in result
    assert result["hw"]["platform"]["type"] == "rpi"


async def test_get_info_legacy_drops_health_from_the_default_set(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd builds the default set from its submanagers and then removes
    # `health` from it, so "no fields" is not "every category".
    mock_api.get("/api/info").mock(
        return_value=httpx.Response(200, json=load_json("info"))
    )
    result = await client.system.get_info()
    assert "health" not in result
    # It moved: the legacy shape carries it inside `hw`.
    assert "health" in result["hw"]


async def test_get_info_legacy_takes_platform_out_of_system(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/info").mock(
        return_value=httpx.Response(200, json=load_json("info_hw_system"))
    )
    result = await client.system.get_info("hw", "system")
    request = mock_api.calls[-1].request
    # kvmd reads `fields` as a single comma-separated value; repeated
    # params (fields=a&fields=b) would drop all fields except the first.
    assert request.url.params.get_list("fields") == ["hw,system"]
    assert "platform" in result["hw"]
    assert "platform" not in result["system"]


async def test_get_info_hw_alone_drops_system_entirely(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # `hw` is assembled out of `health` and `system`, both of which kvmd
    # fetches to build it — and then discards `system` unasked.
    mock_api.get("/api/info").mock(
        return_value=httpx.Response(200, json=load_json("info_hw"))
    )
    result = await client.system.get_info("hw")
    request = mock_api.calls[-1].request
    assert request.url.params.get_list("fields") == ["hw"]
    assert sorted(result) == ["hw"]
    assert sorted(result["hw"]) == ["health", "platform"]


async def test_get_info_modern_shape(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/info").mock(
        return_value=httpx.Response(200, json=load_json("info_legacy0"))
    )
    result = await client.system.get_info(legacy=False)
    request = mock_api.calls[-1].request
    assert request.url.params.get_list("legacy") == ["0"]
    assert "fields" not in request.url.params
    # Every submanager, none of the rearrangement.
    assert "hw" not in result
    assert "health" in result
    assert "platform" in result["system"]


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


STREAM_LOG_TEXT = "line1\nline2\nline3\n"


async def test_stream_log(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/log").mock(
        return_value=httpx.Response(200, text=STREAM_LOG_TEXT)
    )
    lines = [line async for line in client.system.stream_log()]
    assert lines == ["line1", "line2", "line3"]


async def test_stream_log_with_seek(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/log").mock(
        return_value=httpx.Response(200, text=STREAM_LOG_TEXT)
    )
    lines = [line async for line in client.system.stream_log(seek=3600)]
    request = mock_api.calls[-1].request
    url = str(request.url)
    assert "seek=3600" in url
    assert "follow=1" in url
    assert len(lines) == 3


async def test_stream_log_follow_param(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/log").mock(return_value=httpx.Response(200, text="log entry\n"))
    _ = [line async for line in client.system.stream_log()]
    request = mock_api.calls[-1].request
    assert "follow=1" in str(request.url)


async def test_stream_log_auth_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/log").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        async for _ in client.system.stream_log():
            pass
