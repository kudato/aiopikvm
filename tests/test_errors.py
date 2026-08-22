"""Error handling tests — transport failures, error statuses, bad payloads."""

import re
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from aiopikvm import (
    APIError,
    AuthError,
    AuthMode,
    BusyError,
    ConfigurationError,
    ConnectError,
    ConnectionTimeoutError,
    PiKVM,
    PiKVMError,
    RedirectError,
    ResponseError,
    UnavailableError,
)

ATX_OK = {
    "ok": True,
    "result": {
        "enabled": True,
        "busy": False,
        "acts": {"power": False, "reset": False},
        "leds": {"power": False, "hdd": False},
    },
}


def _kvmd_error(error: str, error_msg: str) -> dict[str, object]:
    """Build the error envelope kvmd returns for a failed request."""
    return {"ok": False, "result": {"error": error, "error_msg": error_msg}}


def _corrupt_gzip() -> httpx.Response:
    """Build a response whose body does not survive its `Content-Encoding`.

    The bytes go in as an async iterator rather than as ``content=b"..."``:
    httpx decodes a whole body inside `httpx.Response.__init__`, so a plain
    one would fail while the test was still writing the mock rather than
    where the client reads it.

    Returns:
        A 200 announcing gzip and carrying something else.
    """

    async def body() -> AsyncIterator[bytes]:
        yield b"not gzip at all"

    return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=body())


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


async def test_redfish_action_ignores_the_body(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A 2xx is the whole success signal: kvmd sends 204 and nothing else."""
    mock_api.post("/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    assert await client.redfish.reset() is None


async def test_invalid_json_redfish_document(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/redfish/v1/Systems/0").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    with pytest.raises(ResponseError, match="not JSON"):
        await client.redfish.get_system()


async def test_remote_protocol_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    # kvmd runs with shutdown_timeout=1 and drops in-flight connections on
    # restart, which httpx reports as RemoteProtocolError.
    mock_api.get("/api/atx").mock(
        side_effect=httpx.RemoteProtocolError("Server disconnected")
    )
    with pytest.raises(ConnectError, match="Server disconnected"):
        await client.atx.get_state()


async def test_read_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(side_effect=httpx.ReadError("Connection reset"))
    with pytest.raises(ConnectError, match="Connection reset"):
        await client.atx.get_state()


async def test_decoding_error_stays_in_the_hierarchy(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """httpx derives DecodingError from RequestError, not TransportError.

    kvmd's nginx gzips text responses, so a body that does not survive its
    ``Content-Encoding`` is reachable over a lossy link. The bytes arrived —
    calling that a connection failure would point at the wrong thing.
    """
    mock_api.get("/api/atx").mock(return_value=_corrupt_gzip())
    with pytest.raises(ResponseError, match="Content-Encoding"):
        await client.atx.get_state()


async def test_decoding_error_while_streaming(mock_api: respx.MockRouter) -> None:
    """The same gap on the streaming path, where the body is read by the caller.

    `stream()` is suspended at its ``yield`` while the caller iterates, and a
    failure there is thrown back into it — so the one translation block covers
    both halves.
    """
    mock_api.get("/api/log").mock(return_value=_corrupt_gzip())
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(ResponseError, match="Content-Encoding"):
            async for _ in kvm.system.stream_log():
                pass  # pragma: no cover - the first read fails


async def test_url_without_scheme() -> None:
    async with PiKVM("pikvm.local", user="admin", passwd="admin") as kvm:
        with pytest.raises(ConfigurationError, match=re.escape("https://pikvm.local")):
            await kvm.atx.get_state()


async def test_non_ascii_credentials() -> None:
    with pytest.raises(ConfigurationError, match="ASCII"):
        async with PiKVM("https://pikvm.local", user="admin", passwd="паролü"):
            pass  # pragma: no cover - __aenter__ raises


@pytest.mark.parametrize("auth", ["headers", "basic"])
async def test_non_ascii_totp_code(mock_api: respx.MockRouter, auth: AuthMode) -> None:
    """The one credential component `__aenter__` cannot check for itself.

    A callable produces a new code per request, so an unusable one only shows
    up where the headers are built. ``"headers"`` used to raise a bare
    `UnicodeEncodeError` there; ``"basic"`` did not raise at all, and sent the
    credential UTF-8 encoded for kvmd to read as something else.

    No route is registered: the point is that nothing is sent, and an
    unmatched request would fail the test by itself.
    """
    async with PiKVM(
        "https://pikvm.local",
        user="admin",
        passwd="secret",
        totp=lambda: "код",
        auth=auth,
    ) as kvm:
        with pytest.raises(ConfigurationError, match="ASCII"):
            await kvm.atx.get_state()
    assert not mock_api.calls


async def test_busy_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/atx/power").mock(
        return_value=httpx.Response(
            409,
            json=_kvmd_error(
                "AtxIsBusyError",
                "Performing another ATX operation, please try again later",
            ),
        )
    )
    with pytest.raises(BusyError, match="Performing another ATX operation") as info:
        await client.atx.power_on()
    assert info.value.status_code == 409
    assert info.value.error == "AtxIsBusyError"


async def test_unavailable_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd").mock(
        return_value=httpx.Response(
            503, json=_kvmd_error("UnavailableError", "Service Unavailable")
        )
    )
    with pytest.raises(UnavailableError, match="Service Unavailable") as info:
        await client.msd.get_state()
    assert info.value.status_code == 503


async def test_error_msg_surfaced(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_key").mock(
        return_value=httpx.Response(
            400, json=_kvmd_error("ValidatorError", "The argument 'nope' is not a key")
        )
    )
    with pytest.raises(APIError, match="The argument 'nope' is not a key") as info:
        await client.hid.send_key("nope")
    assert info.value.error == "ValidatorError"


async def test_auth_error_carries_kvmd_fields(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            403, json=_kvmd_error("ForbiddenError", "Forbidden")
        )
    )
    with pytest.raises(AuthError) as info:
        await client.atx.get_state()
    assert info.value.error == "ForbiddenError"
    assert info.value.status_code == 403


async def test_error_status_without_kvmd_body(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(APIError, match="HTTP 502: bad gateway") as info:
        await client.atx.get_state()
    assert info.value.error == ""


async def test_redirect_reported(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://pikvm.local/api/atx/"}
        )
    )
    expected = re.escape("https://pikvm.local/api/atx/")
    with pytest.raises(RedirectError, match=expected) as info:
        await client.atx.get_state()
    assert info.value.status_code == 301


async def test_redirect_followed_when_enabled(mock_api: respx.MockRouter) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://pikvm.local/api/atx/"}
        )
    )
    mock_api.get("/api/atx/").mock(return_value=httpx.Response(200, json=ATX_OK))
    async with PiKVM("https://pikvm.local", follow_redirects=True) as kvm:
        assert (await kvm.atx.get_state()).enabled is True


async def test_redirect_loop_stays_in_the_hierarchy(
    mock_api: respx.MockRouter,
) -> None:
    """httpx derives TooManyRedirects from RequestError, not TransportError."""
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://pikvm.local/api/atx"}
        )
    )
    async with PiKVM("https://pikvm.local", follow_redirects=True) as kvm:
        with pytest.raises(RedirectError, match="edirect"):
            await kvm.atx.get_state()


async def test_unparsable_payload(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"enabled": True}})
    )
    with pytest.raises(ResponseError, match="ATXState cannot parse"):
        await client.atx.get_state()


async def test_unparsable_payload_stays_in_the_hierarchy(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/gpio").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"nope": 1}})
    )
    with pytest.raises(PiKVMError):
        await client.gpio.get_state()


async def test_json_list_body(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(ResponseError, match="expected an object"):
        await client.atx.get_state()


async def test_error_status_while_streaming(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The body is unread inside stream(); reading it is what keeps this from
    # failing with httpx.ResponseNotRead instead of an aiopikvm error.
    mock_api.get("/api/log").mock(
        return_value=httpx.Response(
            503, json=_kvmd_error("UnavailableError", "Service Unavailable")
        )
    )
    with pytest.raises(UnavailableError, match="Service Unavailable"):
        async for _ in client.system.stream_log():
            pass  # pragma: no cover - the request fails before yielding


async def test_redirect_while_streaming(mock_api: respx.MockRouter) -> None:
    """The unread body must not be touched to report a redirect."""
    mock_api.get("/api/log").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://pikvm.local/api/log/"}
        )
    )
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(RedirectError, match="api/log/"):
            async for _ in kvm.system.stream_log():
                pass  # pragma: no cover - the request fails before yielding


async def test_timeout_while_streaming(mock_api: respx.MockRouter) -> None:
    """The streaming path wraps a timeout the same way the plain one does."""
    mock_api.get("/api/log").mock(side_effect=httpx.ReadTimeout("Read timed out"))
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(ConnectionTimeoutError, match="Read timed out"):
            async for _ in kvm.system.stream_log():
                pass  # pragma: no cover - the request fails before yielding


async def test_url_without_scheme_while_streaming() -> None:
    """And the same missing scheme, which httpx reports before connecting."""
    async with PiKVM("pikvm.local", user="admin", passwd="admin") as kvm:
        with pytest.raises(ConfigurationError, match=re.escape("https://pikvm.local")):
            async for _ in kvm.system.stream_log():
                pass  # pragma: no cover - the request fails before yielding


async def test_redirect_loop_while_streaming(mock_api: respx.MockRouter) -> None:
    """The same httpx.TooManyRedirects gap exists on the streaming path."""
    mock_api.get("/api/log").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://pikvm.local/api/log"}
        )
    )
    async with PiKVM("https://pikvm.local", follow_redirects=True) as kvm:
        with pytest.raises(RedirectError, match="edirect"):
            async for _ in kvm.system.stream_log():
                pass  # pragma: no cover - the request fails before yielding


async def test_per_call_timeout_override(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    route = mock_api.post("/api/atx/click").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.atx.click_power_long(timeout=30.0)
    assert route.calls.last.request.extensions["timeout"]["read"] == 30.0


async def test_client_timeout_used_by_default(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    route = mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=ATX_OK))
    await client.atx.get_state()
    assert route.calls.last.request.extensions["timeout"]["read"] == 10.0
