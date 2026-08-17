"""PiKVM client lifecycle tests."""

import importlib.util
import ssl
import urllib.request
from pathlib import Path
from typing import Any

import certifi
import httpx
import pytest
import respx

from aiopikvm import ConfigurationError, ConnectError, PiKVM, PiKVMError
from aiopikvm._base_resource import BaseResource
from aiopikvm._client import _RESOURCE_NAMES


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
    async with PiKVM("https://pikvm.local", user="admin", passwd="secret") as client:
        assert client._client is not None
        assert client._client.headers["X-KVMD-User"] == "admin"
        assert client._client.headers["X-KVMD-Passwd"] == "secret"


async def test_totp_concat(mock_api: respx.MockRouter) -> None:
    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="secret", totp="123456"
    ) as client:
        assert client._client is not None
        assert client._client.headers["X-KVMD-Passwd"] == "secret123456"


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


async def test_ws_inherits_the_proxy(mock_api: respx.MockRouter) -> None:
    """One proxy setting for the device, not one per protocol (#69)."""
    async with PiKVM(
        "https://pikvm.local", proxy="http://proxy.local:3128", trust_env=False
    ) as client:
        ws = client.ws()
        assert ws._proxy == "http://proxy.local:3128"
        assert ws._trust_env is False


async def test_a_ca_bundle_is_loaded_once_for_both_transports() -> None:
    """The socket verifies against the same context the requests do (#69).

    websockets takes no path at all and httpx only takes one under a
    deprecation warning, so the bundle is read here — and a bundle read
    twice would be two objects, not one.
    """
    async with PiKVM("https://pikvm.local", verify_ssl=certifi.where()) as client:
        assert isinstance(client._verify_ssl, ssl.SSLContext)
        assert client.ws()._verify_ssl is client._verify_ssl


def test_an_unusable_ca_bundle_is_refused_at_construction(tmp_path: Path) -> None:
    """The path is wrong now, rather than once a request is attempted (#69)."""
    with pytest.raises(ConfigurationError, match="Cannot verify TLS against"):
        PiKVM("https://pikvm.local", verify_ssl=tmp_path / "missing.pem")


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
    with pytest.raises(ConfigurationError, match="the PiKVM URL"):
        async with PiKVM("http://[::1"):
            pass  # pragma: no cover - __aenter__ raises


async def test_an_invalid_url_is_blamed_alone_when_no_proxy_could_share_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in the URL should not be reported as maybe-the-proxy (#69).

    httpx reports a proxy it cannot parse the same way it reports a URL it
    cannot parse, so the environment is asked whether there is a proxy at
    all before the message hedges.
    """
    monkeypatch.setattr(urllib.request, "getproxies", dict)
    with pytest.raises(ConfigurationError) as caught:
        async with PiKVM("http://[::1"):
            pass  # pragma: no cover - __aenter__ raises
    assert "proxy" not in str(caught.value)

    monkeypatch.setattr(
        urllib.request, "getproxies", lambda: {"http": "http://proxy.local:3128"}
    )
    with pytest.raises(ConfigurationError) as caught:
        async with PiKVM("http://[::1"):
            pass  # pragma: no cover - __aenter__ raises
    assert "proxy" in str(caught.value)


def _spy_on_httpx(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record the keyword arguments the client builds httpx with.

    An ``httpx.AsyncClient`` reads none of these back once it holds them, so
    what it was handed is the only place the wiring can be checked.

    Args:
        monkeypatch: The fixture used to swap the class out.

    Returns:
        The dictionary the arguments are recorded into.
    """
    captured: dict[str, Any] = {}
    build = httpx.AsyncClient

    def spy(**kwargs: Any) -> httpx.AsyncClient:
        captured.update(kwargs)
        return build(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    return captured


async def test_tls_and_proxy_settings_reach_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setting nothing forwards is a setting that does nothing (#69)."""
    captured = _spy_on_httpx(monkeypatch)
    context = ssl.create_default_context()
    async with PiKVM(
        "https://pikvm.local",
        verify_ssl=context,
        proxy="http://proxy.local:3128",
        trust_env=False,
    ):
        pass
    assert captured["verify"] is context
    assert captured["proxy"] == "http://proxy.local:3128"
    assert captured["trust_env"] is False


async def test_the_defaults_leave_httpx_where_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No proxy of our own, and the environment read as httpx always did."""
    captured = _spy_on_httpx(monkeypatch)
    async with PiKVM("https://pikvm.local"):
        pass
    assert captured["verify"] is False
    assert captured["proxy"] is None
    assert captured["trust_env"] is True


async def test_a_proxy_scheme_httpx_alone_refuses_is_a_configuration_error() -> None:
    """httpx raises a bare ValueError, which is outside the hierarchy (#69).

    ``socks4://`` is the gap between the two libraries in the direction the
    constructor does not close: *websockets* parses it, so it reaches httpx,
    which knows only ``socks5``.
    """
    with pytest.raises(ConfigurationError, match="Cannot use the proxy"):
        async with PiKVM("https://pikvm.local", proxy="socks4://proxy.local:1080"):
            pass  # pragma: no cover - __aenter__ raises


def test_a_bad_proxy_port_is_not_blamed_on_the_pikvm_url() -> None:
    """httpx reports it as InvalidURL, the same as a bad base URL would be.

    Caught before httpx sees it, so the message names the argument that is
    actually wrong instead of accusing a URL that is fine (#69).
    """
    with pytest.raises(ConfigurationError) as caught:
        PiKVM("https://pikvm.local", proxy="http://proxy.local:notaport")
    message = str(caught.value)
    assert "http://proxy.local:notaport" in message
    assert "PiKVM URL" not in message


async def test_a_connect_failure_httpx_has_no_name_for_stays_in_the_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx maps what it knows and lets the rest out as a group (#69).

    A proxy port above 65535 is one it builds a client with and only fails
    on at connect time, inside the task group anyio connects in, as an
    ``ExceptionGroup`` that prints how many exceptions it holds and none of
    what they say. Which variable httpx reads for a given URL, and whether
    ``NO_PROXY`` exempts the host, is its own business — reproducing those
    rules from outside gets them wrong both ways round. What is owed here is
    that the group does not reach the caller, and that the message says
    enough to find the variable.
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:99999")
    async with PiKVM("https://127.0.0.1:9") as kvm:
        with pytest.raises(ConnectError) as caught:
            await kvm.request("GET", "/api/atx")
    message = str(caught.value)
    assert "0-65535" in message
    assert "HTTPS_PROXY" in message


async def test_a_streaming_connect_failure_stays_in_the_hierarchy_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stream()`` opens its own connection, so it meets the same group.

    An MSD image download is the call that goes through it, and a group
    escaping there would be no less outside ``PiKVMError`` (#69).
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:99999")
    async with PiKVM("https://127.0.0.1:9") as kvm:
        with pytest.raises(ConnectError, match="0-65535"):
            async with kvm.stream("GET", "/api/msd/read"):
                pass  # pragma: no cover - the connection never opens


async def test_an_ordinary_connect_failure_reads_as_it_always_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused connection is a ``TransportError``, mapped by httpx itself.

    It never reaches the group clause, and the message stays httpx's own
    rather than gaining a paragraph about proxy variables (#69).
    """
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setattr("urllib.request.getproxies", dict)
    async with PiKVM("https://127.0.0.1:9") as kvm:
        with pytest.raises(ConnectError) as caught:
            await kvm.request("GET", "/api/atx")
    assert "PROXY" not in str(caught.value)


def test_what_a_contained_group_is_made_to_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The group prints its own count and nothing else, so this says it all.

    Where a proxy could be standing in the way is worth naming, and the
    environment is named by variable and never by value: it is shared with
    every other program on the machine and the URL in it can carry
    credentials (#69).
    """
    group = ExceptionGroup("connecting", [OverflowError("port must be 0-65535")])
    named = {"https": "http://user:s3cret@proxy.local:3128"}
    monkeypatch.setattr("urllib.request.getproxies", lambda: named)
    monkeypatch.setattr("urllib.request.getproxies_environment", lambda: named)

    read = PiKVM("https://pikvm.local")._connection_failed(group)
    assert "OverflowError: port must be 0-65535" in read
    assert "HTTPS_PROXY" in read
    assert "s3cret" not in read

    ignored = PiKVM("https://pikvm.local", trust_env=False)._connection_failed(group)
    assert "PROXY" not in ignored

    passed = PiKVM(
        "https://pikvm.local", proxy="http://proxy.local:3128"
    )._connection_failed(group)
    assert "http://proxy.local:3128" in passed


def test_a_proxy_no_variable_set_is_not_blamed_on_a_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``getproxies()`` answers for macOS and Windows too, and httpx reads it.

    With nothing in the environment it falls back on what the machine has
    been configured with system-wide. Those settings genuinely reach httpx,
    so they are worth naming — but calling them ``HTTPS_PROXY`` would invent
    a variable nobody set, and send the reader looking for it (#69).
    """
    monkeypatch.setattr(
        "urllib.request.getproxies", lambda: {"https": "http://proxy.local:3128"}
    )
    monkeypatch.setattr("urllib.request.getproxies_environment", dict)

    read = PiKVM("https://pikvm.local")._connection_failed(
        ExceptionGroup("connecting", [OverflowError("port must be 0-65535")])
    )
    assert "HTTPS_PROXY" not in read
    assert "this machine is configured with a proxy for https" in read


async def test_a_group_the_caller_raised_is_left_alone(
    mock_api: respx.MockRouter,
) -> None:
    """``stream()`` yields inside its own try, so their block reports here.

    That is what maps a transport failure met while the body is read, and it
    would just as happily read a task group of the caller's own making as a
    failure to connect — turning their error into a ``ConnectError`` that
    says nothing about what they were doing (#69).
    """
    mock_api.get("/api/msd/read").respond(200, content=b"payload")
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(ExceptionGroup) as caught:
            async with kvm.stream("GET", "/api/msd/read"):
                raise ExceptionGroup("mine", [ValueError("my own failure")])
    assert caught.value.exceptions[0].args[0] == "my own failure"


async def test_a_transport_failure_the_caller_wrapped_is_still_contained(
    mock_api: respx.MockRouter,
) -> None:
    """Whose group it is does not decide what is inside it (#69).

    A body read run inside a task group of the caller's own comes back
    wrapped, and refusing every group once the response is theirs let a
    connection failure out bare. A group whose every leaf is one of httpx's
    transport errors is a connection failure however it was wrapped.
    """
    mock_api.get("/api/msd/read").respond(200, content=b"payload")
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(ConnectError, match="the peer hung up"):
            async with kvm.stream("GET", "/api/msd/read"):
                raise ExceptionGroup(
                    "reading", [httpx.RemoteProtocolError("the peer hung up")]
                )


async def test_a_group_holding_both_is_left_to_the_caller(
    mock_api: respx.MockRouter,
) -> None:
    """One of the two has to survive, and theirs is the one nothing else holds.

    A ``ConnectError`` can carry the transport failure and not the rest, so
    the group goes on as it is and keeps both (#69).
    """
    mock_api.get("/api/msd/read").respond(200, content=b"payload")
    async with PiKVM("https://pikvm.local") as kvm:
        with pytest.raises(ExceptionGroup) as caught:
            async with kvm.stream("GET", "/api/msd/read"):
                raise ExceptionGroup(
                    "reading",
                    [
                        httpx.RemoteProtocolError("the peer hung up"),
                        ValueError("and my own code failed too"),
                    ],
                )
    assert len(caught.value.exceptions) == 2


def test_a_group_holding_a_group_still_says_what_went_wrong() -> None:
    """anyio nests one task group inside another, and only leaves talk."""
    nested = ExceptionGroup(
        "outer", [ExceptionGroup("inner", [ValueError("the port was hopeless")])]
    )
    said = PiKVM("https://pikvm.local")._connection_failed(nested)
    assert "ValueError: the port was hopeless" in said


async def test_an_unusable_ca_bundle_in_the_environment_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """httpx reads SSL_CERT_FILE itself, and reports it as a bare OSError.

    Only when *verify_ssl* is ``True``: that is the one case where httpx
    builds the context rather than being handed one (#69).
    """
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(ConfigurationError, match="SSL_CERT_FILE"):
        async with PiKVM("https://pikvm.local", verify_ssl=True):
            pass  # pragma: no cover - __aenter__ raises


@pytest.mark.skipif(
    importlib.util.find_spec("socksio") is not None,
    reason="socksio is installed, so httpx builds the transport instead",
)
async def test_a_socks_proxy_without_socksio_is_a_configuration_error() -> None:
    """httpx raises ImportError, which is outside the hierarchy too (#69)."""
    with pytest.raises(ConfigurationError, match="Cannot use the proxy"):
        async with PiKVM("https://pikvm.local", proxy="socks5://proxy.local:1080"):
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
