"""PiKVM client lifecycle tests."""

import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

import aiopikvm
from aiopikvm import ConfigurationError, PiKVM, PiKVMError, RedirectError
from aiopikvm._base_resource import BaseResource
from aiopikvm._client import _RESOURCE_NAMES
from tests.fixtures import load_json

OK = {"ok": True, "result": {}}


async def test_context_manager(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as client:
        assert client._client is not None
        assert client.atx is not None
        assert client.hid is not None
        assert client.msd is not None
        assert client.gpio is not None
        assert client.streamer is not None
        assert client.switch is not None
        assert client.redfish is not None
        assert client.prometheus is not None
        assert client.auth is not None


async def test_auth_headers(mock_api: respx.MockRouter) -> None:
    # Asserted on the request rather than the client: the credentials are
    # built per call, so that a rotating TOTP code is the current one.
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM("https://pikvm.local", user="admin", passwd="secret") as client:
        await client.request("GET", "/api/atx")
    request = mock_api.calls[-1].request
    assert request.headers["X-KVMD-User"] == "admin"
    assert request.headers["X-KVMD-Passwd"] == "secret"


async def test_totp_concat(mock_api: respx.MockRouter) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="secret", totp="123456"
    ) as client:
        await client.request("GET", "/api/atx")
    assert mock_api.calls[-1].request.headers["X-KVMD-Passwd"] == "secret123456"


async def test_ws_factory(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as client:
        assert client.ws()._url == "wss://pikvm.local/api/ws?stream=1"
        assert client.ws(stream=False)._url == "wss://pikvm.local/api/ws?stream=0"


@pytest.mark.parametrize("follow", [False, True])
async def test_ws_inherits_follow_redirects(
    mock_api: respx.MockRouter, follow: bool
) -> None:
    """A followed redirect resends the password, so the socket must not decide."""
    async with PiKVM("https://pikvm.local", follow_redirects=follow) as client:
        assert client.ws()._follow_redirects is follow


async def test_ws_inherits_verify_ssl(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", verify_ssl=True) as client:
        assert client.ws()._verify_ssl is True


async def test_client_close(mock_api: respx.MockRouter) -> None:
    client = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    async with client:
        assert client._client is not None
    assert client._client is None


def test_access_before_aenter() -> None:
    client = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    for name in _RESOURCE_NAMES:
        with pytest.raises(PiKVMError, match="async context"):
            getattr(client, name)


def test_access_unknown_attr() -> None:
    client = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    with pytest.raises(AttributeError):
        client.nonexistent  # noqa: B018


async def test_external_http_client_not_closed(mock_api: respx.MockRouter) -> None:
    ext_client = httpx.AsyncClient(
        base_url="https://pikvm.local",
        headers={"X-KVMD-User": "admin", "X-KVMD-Passwd": "admin"},
    )
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="admin", http_client=ext_client
    ):
        pass
    assert not ext_client.is_closed
    await ext_client.aclose()


async def test_resources_cleared_after_aexit(mock_api: respx.MockRouter) -> None:
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        _ = kvm.atx
        _ = kvm.hid
        assert "atx" in kvm.__dict__
        assert "hid" in kvm.__dict__
    assert "atx" not in kvm.__dict__
    assert "hid" not in kvm.__dict__


async def test_explicit_aclose(mock_api: respx.MockRouter) -> None:
    kvm = PiKVM("https://pikvm.local", user="admin", passwd="admin")
    await kvm.__aenter__()
    _ = kvm.atx
    assert kvm._client is not None
    assert "atx" in kvm.__dict__
    await kvm.aclose()
    assert kvm._client is None
    assert "atx" not in kvm.__dict__


async def test_external_client_is_released_on_close(
    mock_api: respx.MockRouter,
) -> None:
    """Closing lets go of an injected client without closing it (#70).

    The reference was kept before, so every resource went on working after
    the block that owned them ended — and went on using an HTTP client the
    caller was free to have closed by then.
    """
    ext_client = httpx.AsyncClient(base_url="https://pikvm.local")
    kvm = PiKVM("https://pikvm.local", http_client=ext_client)
    async with kvm:
        assert kvm.atx is not None
    assert not ext_client.is_closed
    assert kvm._client is None
    await ext_client.aclose()


@pytest.mark.parametrize("external", [False, True])
async def test_resources_refuse_to_work_after_close(
    mock_api: respx.MockRouter, external: bool
) -> None:
    """Whoever owns the HTTP client, a closed PiKVM serves nothing (#70)."""
    ext_client = httpx.AsyncClient(base_url="https://pikvm.local")
    kvm = PiKVM("https://pikvm.local", http_client=ext_client if external else None)
    async with kvm:
        pass
    for name in _RESOURCE_NAMES:
        with pytest.raises(PiKVMError, match="has been closed"):
            getattr(kvm, name)
    with pytest.raises(PiKVMError, match="has been closed"):
        kvm.base_url  # noqa: B018
    with pytest.raises(PiKVMError, match="has been closed"):
        kvm.cookies  # noqa: B018
    await ext_client.aclose()


async def test_request_refuses_after_close(mock_api: respx.MockRouter) -> None:
    """The failure names the cause instead of the not-entered-yet one."""
    kvm = PiKVM("https://pikvm.local")
    async with kvm:
        pass
    with pytest.raises(PiKVMError, match="has been closed"):
        await kvm.request("GET", "/api/atx")


async def test_reopening_is_refused(mock_api: respx.MockRouter) -> None:
    """A closed client cannot be reopened, exactly as in httpx (#70).

    Entering again used to build a second connection pool under the same
    object, rereading the credentials as they stood at that moment.
    """
    kvm = PiKVM("https://pikvm.local")
    async with kvm:
        pass
    with pytest.raises(ConfigurationError, match="reopen"):
        async with kvm:
            pass  # pragma: no cover - __aenter__ raises


async def test_entering_twice_is_refused(mock_api: respx.MockRouter) -> None:
    """The inner block would close what the outer one is still using (#70)."""
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(ConfigurationError, match="more than once"):
            async with kvm:
                pass  # pragma: no cover - __aenter__ raises
        # The outer block is untouched by the refusal.
        assert kvm._client is not None


async def test_aclose_is_idempotent(mock_api: respx.MockRouter) -> None:
    """``async with`` already closed it; calling again is not an error."""
    kvm = PiKVM("https://pikvm.local")
    async with kvm:
        pass
    await kvm.aclose()
    assert kvm._client is None


async def test_aclose_without_entering(mock_api: respx.MockRouter) -> None:
    """Closing a client that was never opened still closes it, as in httpx."""
    kvm = PiKVM("https://pikvm.local")
    await kvm.aclose()
    with pytest.raises(ConfigurationError, match="reopen"):
        async with kvm:
            pass  # pragma: no cover - __aenter__ raises


async def test_invalid_url_is_a_configuration_error() -> None:
    """httpx's own InvalidURL would land outside the hierarchy."""
    with pytest.raises(ConfigurationError, match="Cannot build an HTTP client"):
        async with PiKVM("http://[::1"):
            pass  # pragma: no cover - __aenter__ raises


async def test_invalid_proxy_url_is_a_configuration_error() -> None:
    """httpx raises a plain ValueError for one, which is not an InvalidURL."""
    with pytest.raises(ConfigurationError, match="Cannot build an HTTP client"):
        async with PiKVM("https://pikvm.local", proxy="nonsense://proxy"):
            pass  # pragma: no cover - __aenter__ raises


async def test_invalid_proxy_in_the_environment_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The program passed nothing wrong; trust_env is on by default.

    The suite-wide `no_machine_proxy` fixture leaves ``no_proxy=*`` behind,
    and httpx reads that star as a blanket bypass and never looks at
    ``HTTPS_PROXY`` at all. This test is about the machine that *does* have a
    proxy configured, so it puts one back.
    """
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "nonsense://proxy")
    with pytest.raises(ConfigurationError, match="Cannot build an HTTP client"):
        async with PiKVM("https://pikvm.local"):
            pass  # pragma: no cover - __aenter__ raises


async def test_ws_refuses_after_close(mock_api: respx.MockRouter) -> None:
    """The socket does not go through httpx, so it needs its own guard (#70)."""
    kvm = PiKVM("https://pikvm.local")
    async with kvm:
        assert kvm.ws() is not None
    with pytest.raises(ConfigurationError, match="has been closed"):
        kvm.ws()


async def test_ws_before_entering(mock_api: respx.MockRouter) -> None:
    """A socket needs no HTTP client, and could always be built without one."""
    kvm = PiKVM("https://pikvm.local")
    assert kvm.ws()._url == "wss://pikvm.local/api/ws?stream=1"


async def test_ws_factory_uses_timeout(mock_api: respx.MockRouter) -> None:
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="admin", timeout=5.0
    ) as kvm:
        ws = kvm.ws()
        assert ws._open_timeout == 5.0
        assert ws._close_timeout == 5.0

        ws2 = kvm.ws(open_timeout=2.0, close_timeout=3.0)
        assert ws2._open_timeout == 2.0
        assert ws2._close_timeout == 3.0


async def test_patch_request(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.patch("/api/test").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"patched": True}})
    )
    resource = BaseResource(client)
    result = await resource._patch("/api/test", json={"key": "value"})
    assert result == {"patched": True}


async def test_a_refused_stream_closes_the_context_that_owns_it(
    mock_api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A status `stream()` refuses leaves nothing suspended behind it.

    `httpx.AsyncClient.stream()` is a context manager, and exiting it is what
    hands the connection back. `stream()` connects before it knows whether
    the status is one it will report, so the attempt that turns out to be a
    failure has to exit that context itself — the caller never gets an
    ``async with`` to do it for them.

    A redirect is what shows it: `stream()` reads the body of a 4xx before
    reporting it, and reading a response to its end closes the response in
    httpx anyway, which hides the difference. A 3xx is reported from its
    ``Location`` alone.
    """
    exited: list[BaseException | None] = []
    opened = httpx.AsyncClient.stream

    def watched(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> Any:
        inner = opened(self, *args, **kwargs)

        @asynccontextmanager
        async def watch() -> AsyncIterator[httpx.Response]:
            async with inner as response:
                try:
                    yield response
                finally:
                    exited.append(sys.exception())

        return watch()

    monkeypatch.setattr(httpx.AsyncClient, "stream", watched)
    mock_api.get("/api/log").mock(
        return_value=httpx.Response(302, headers={"Location": "https://elsewhere/"})
    )
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        with pytest.raises(RedirectError):
            [line async for line in kvm.system.stream_log()]
        assert [type(exc) for exc in exited] == [RedirectError]


_STREAMS: dict[str, tuple[str, str]] = {
    "stream_log": ("GET", "/api/log"),
    "download": ("GET", "/api/msd/read"),
    "upload_remote_progress": ("POST", "/api/msd/write_remote"),
    "mjpeg": ("GET", "/streamer/stream"),
}
"""Every streaming call, and the request each one makes.

`test_no_streaming_call_is_missing_from_this_list` is what keeps it that way:
the bug these tests are about was one call site drifting from the others, so a
list of call sites that quietly falls behind would be the same mistake again.
"""


def _stream_route(mock_api: respx.MockRouter, call: str) -> None:
    """Answer *call*'s endpoint with a stream that ends where it starts.

    The body is empty because nothing here reads one: these tests assert on
    the request that went out, and an empty stream is the shortest way to get
    one sent and the connection back.

    Args:
        mock_api: The router to register on.
        call: Which streaming call, keyed as in ``_STREAMS``.
    """
    (method, path) = _STREAMS[call]
    headers = {}
    if call == "mjpeg":
        # The one header read before the body: mjpeg() refuses an answer that
        # is not a multipart stream. Taken from the recording rather than
        # retyped, since the device writes no space after the semicolon.
        headers["Content-Type"] = _recorded_stream_content_type()
    mock_api.request(method, path).mock(
        return_value=httpx.Response(200, content=b"", headers=headers)
    )


def _recorded_stream_content_type() -> str:
    """Return the ``Content-Type`` ustreamer's MJPEG stream arrived with.

    Returns:
        The value recorded in the ``media_stream`` scenario.

    Raises:
        KeyError: If the scenario no longer has the step it is read from.
    """
    for recorded in load_json("media_stream")["steps"]:
        if recorded["name"] == "stream_plain":
            content_type: str = recorded["content_type"]
            return content_type
    raise KeyError("media_stream has no stream_plain step to read a content type from")


def _streaming_call(
    kvm: PiKVM, call: str, timeout: float | httpx.Timeout | None = None
) -> AsyncIterator[Any]:
    """Open one of the streaming calls, by the name ``_STREAMS`` keys it under.

    Args:
        kvm: The client to call it on.
        call: Which one, keyed as in ``_STREAMS``.
        timeout: Passed on as the call's own *timeout*.

    Returns:
        The iterator, with nothing sent yet — a streaming method makes its
        request when it is first read from.
    """
    calls: dict[str, Callable[[], AsyncIterator[Any]]] = {
        "stream_log": lambda: kvm.system.stream_log(timeout=timeout),
        "download": lambda: kvm.msd.download("boot.iso", timeout=timeout),
        "upload_remote_progress": lambda: kvm.msd.upload_remote_progress(
            "http://host/a.iso", timeout=timeout
        ),
        "mjpeg": lambda: kvm.streamer.mjpeg(timeout=timeout),
    }
    return calls[call]()


@pytest.mark.parametrize("call", list(_STREAMS))
async def test_streaming_keeps_an_injected_clients_timeout(
    mock_api: respx.MockRouter, call: str
) -> None:
    """An injected client's timeout survives a streaming call (#137).

    Each of these built its own `httpx.Timeout` from the *timeout* argument
    this client was given, which the constructor documents as ignored when an
    *http_client* is passed in. A caller who injected a client tuned for a
    slow link got aiopikvm's own connect and write back on exactly the four
    calls where a long transfer is expected.
    """
    _stream_route(mock_api, call)
    ext_client = httpx.AsyncClient(base_url="https://pikvm.local", timeout=60.0)
    async with PiKVM("https://pikvm.local", http_client=ext_client) as kvm:
        assert [record async for record in _streaming_call(kvm, call)] == []
    await ext_client.aclose()
    sent = mock_api.calls[-1].request.extensions["timeout"]
    # The read one is lifted for all of them: a stream has no end to wait for.
    assert sent == {"connect": 60.0, "read": None, "write": 60.0, "pool": 60.0}


@pytest.mark.parametrize("call", list(_STREAMS))
async def test_streaming_takes_a_timeout_of_its_own(
    mock_api: respx.MockRouter, call: str
) -> None:
    """Every streaming call takes an override, ``stream_log()`` too (#137).

    Only the ``stream_log`` case is new behaviour: it was the one streaming
    method with no *timeout* parameter at all, for no stated reason and with
    nothing at the call site to show it. The other three took one before this
    change and passed it through, so those cases record what already held —
    worth keeping for ``mjpeg``, which had no timeout test of its own, and
    for the shape of the parameter list, which is the thing that drifted.
    """
    _stream_route(mock_api, call)
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
        stream = _streaming_call(kvm, call, timeout=httpx.Timeout(5.0, read=90.0))
        assert [record async for record in stream] == []
    sent = mock_api.calls[-1].request.extensions["timeout"]
    # Passed on as given: an override says what it wants of all four fields.
    assert sent == {"connect": 5.0, "read": 90.0, "write": 5.0, "pool": 5.0}


def test_no_streaming_call_is_missing_from_this_list() -> None:
    """A new streaming call has to go through `BaseResource._stream()` (#137).

    That helper is where the streaming timeout default lives. A method that
    opens `PiKVM.stream()` itself gets the client-level read timeout back —
    which is the bug #137 is about, four times over — and neither test above
    would see it, since both drive the calls this file knows.

    Searching the source is what catches it: the resources are the only place
    a streaming endpoint is reached from, and none of them has a reason to
    open one directly.
    """
    resources = Path(cast(str, aiopikvm.__file__)).parent / "resources"
    bypassing = sorted(
        path.name
        for path in resources.glob("*.py")
        if "self._client.stream(" in path.read_text()
    )
    assert bypassing == []
    # And every one that does go through it is exercised above.
    helpers = sorted(
        path.name
        for path in resources.glob("*.py")
        if "self._stream(" in path.read_text()
    )
    assert helpers == ["msd.py", "streamer.py", "system.py"]
