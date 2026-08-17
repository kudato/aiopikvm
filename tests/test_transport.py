"""Connection-setting tests — one setting, understood by both transports."""

import os
import re
import ssl
import tomllib
from pathlib import Path

import certifi
import pytest

from aiopikvm import ConfigurationError
from aiopikvm._transport import (
    httpx_environment_proxies,
    resolve_proxy,
    resolve_verify_ssl,
)


def test_the_websockets_floor_covers_the_module_this_package_imports() -> None:
    """``parse_proxy`` moved into ``websockets.proxy`` in 16.0 (#69).

    Until then it lived in ``websockets.uri``, so a pin that allowed 15.x
    allowed a version where importing this package raises
    ``ModuleNotFoundError`` before anything else can happen. Resolvers pick
    the newest release, so neither the suite nor CI would ever meet the
    version the pin promised to work with — which is why the floor is
    asserted here rather than left to be found by whoever installs it.
    """
    pyproject = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text()
    )
    pins = [
        pin
        for pin in pyproject["project"]["dependencies"]
        if pin.startswith("websockets")
    ]
    assert len(pins) == 1, pins
    floor = re.fullmatch(r"websockets>=\s*(\d+)\.(\d+)", pins[0])
    assert floor is not None, pins[0]
    assert (int(floor[1]), int(floor[2])) >= (16, 0)


def test_bools_pass_straight_through() -> None:
    """Neither names anything to read, so neither is touched here.

    What the transports then do with them differs — httpx takes both as they
    are, while the socket turns ``False`` into a context that verifies
    nothing — but that is their business, not this function's.
    """
    assert resolve_verify_ssl(True) is True
    assert resolve_verify_ssl(False) is False


def test_a_context_is_handed_on_untouched() -> None:
    """Whatever the caller configured on it — a client certificate included."""
    context = ssl.create_default_context()
    assert resolve_verify_ssl(context) is context


@pytest.mark.parametrize("as_path", [False, True])
def test_a_bundle_file_becomes_a_context_that_trusts_it(as_path: bool) -> None:
    """The certificates in the file are the ones the context ends up with."""
    bundle = certifi.where()
    context = resolve_verify_ssl(Path(bundle) if as_path else bundle)
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()


def test_a_directory_becomes_a_context_that_looks_certificates_up(
    tmp_path: Path,
) -> None:
    """A directory is a hashed store, read one certificate per lookup.

    Handing this same path to ``cafile`` would raise ``IsADirectoryError``,
    so the test passing at all is what says the two are told apart — and an
    empty store loading nothing is what says the default certificates were
    not silently added to it. OpenSSL reads such a store lazily, so an empty
    one is accepted here and only fails during a handshake; that is what the
    docstring on ``resolve_verify_ssl`` promises, rather than the immediate
    failure a file gets.
    """
    context = resolve_verify_ssl(tmp_path)
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ca_certs() == []


def test_a_missing_bundle_is_a_configuration_error(tmp_path: Path) -> None:
    """FileNotFoundError would land outside the hierarchy."""
    with pytest.raises(ConfigurationError, match="Cannot verify TLS against"):
        resolve_verify_ssl(tmp_path / "nothing-here.pem")


def test_a_file_holding_no_certificate_is_a_configuration_error(
    tmp_path: Path,
) -> None:
    """ssl reports it as SSLError — an OSError, and outside the hierarchy."""
    junk = tmp_path / "not-a-bundle.pem"
    junk.write_text("this file is not a certificate\n")
    with pytest.raises(ConfigurationError, match="Cannot verify TLS against"):
        resolve_verify_ssl(junk)


def test_a_path_with_a_null_byte_is_a_configuration_error() -> None:
    """The one CA-path failure ssl reports as ValueError, not OSError.

    ``os.path.isdir`` swallows it and answers False, so the path goes to
    ``cafile`` and ``load_verify_locations`` raises — outside the hierarchy
    unless the catch covers more than OSError.
    """
    with pytest.raises(ConfigurationError, match="Cannot verify TLS against"):
        resolve_verify_ssl("ca\x00.pem")


class _UnspeakablePath(os.PathLike[str]):
    """A path object that refuses to say what path it is."""

    def __init__(self, raised: BaseException) -> None:
        self._raised = raised

    def __fspath__(self) -> str:
        raise self._raised


@pytest.mark.parametrize(
    "raised",
    [
        OSError("the filesystem this path came from is gone"),
        RuntimeError("this object was not ready to be asked"),
        TypeError("this object had no idea what was wanted of it"),
    ],
)
def test_a_path_object_that_raises_is_a_configuration_error(
    raised: BaseException,
) -> None:
    """``os.fspath`` runs the caller's own code, which can raise anything.

    A str or a Path never raises here, but the annotation takes any
    ``os.PathLike[str]``, and one of those raising outside the try would
    leave the hierarchy through both public constructors. Narrowing the
    catch to the failures a *path* has would cover only the first of these.
    """
    with pytest.raises(ConfigurationError, match="Cannot read a path out of"):
        resolve_verify_ssl(_UnspeakablePath(raised))


@pytest.mark.parametrize(
    "proxy",
    [
        "http://proxy.local:3128",
        "http://proxy.local",
        "http://proxy.local:3128/",
        "https://proxy.local:3128",
        "http://user:pass@proxy.local:3128",
        "socks5://proxy.local:1080",
    ],
)
def test_a_usable_proxy_passes_through_unchanged(proxy: str) -> None:
    """Nothing is rewritten; the URL is only looked at."""
    assert resolve_proxy(proxy) == proxy


def test_no_proxy_stays_none() -> None:
    """With nothing passed, the libraries are left to their own defaults."""
    assert resolve_proxy(None) is None


@pytest.mark.parametrize(
    ("proxy", "reason"),
    [
        ("http://proxy.local:notaport", "port that is not a number"),
        ("http://proxy.local:99999", "port httpx takes and websockets refuses"),
        ("http://[::1:3128", "unbalanced IPv6 brackets"),
        ("http://", "no host, which httpx takes and websockets refuses"),
        ("http://proxy.local/path", "a path, which httpx ignores"),
        ("http://proxy.local?q=1", "a query, which httpx ignores"),
        ("http://proxy.local#frag", "a fragment, which httpx ignores"),
        ("http://user@proxy.local", "a username httpx would send as user:"),
        ("proxy.local:3128", "no scheme at all"),
        ("", "nothing"),
        ("ftp://proxy.local:1", "a scheme for neither of them"),
    ],
)
def test_a_proxy_only_one_transport_could_use_is_refused(
    proxy: str, reason: str
) -> None:
    """An explicit proxy has to serve both halves of the client (#69).

    Every case here is one httpx would either accept and quietly work
    around, or report in a way that points at something else, while
    *websockets* refuses it outright. Accepting them would mean a setting
    that configures the requests and breaks the socket.
    """
    with pytest.raises(ConfigurationError, match="Cannot use the proxy"):
        resolve_proxy(proxy)


@pytest.mark.parametrize(
    "proxy",
    [
        "socks5://proxy.local",
        "socks5h://proxy.local",
        "socks5://proxy.local:0",
    ],
)
def test_a_socks_proxy_without_a_port_is_refused(proxy: str) -> None:
    """The two libraries would fill the blank in differently (#69).

    httpcore reads a portless socks5 URL as port 1080 and *websockets* reads
    it as port 80, so the one setting would reach two different proxies —
    and neither library would say a word about it. Port ``0`` is the same
    case written differently: both fall back on their default for it.
    """
    with pytest.raises(ConfigurationError, match="needs an explicit port"):
        resolve_proxy(proxy)


def test_only_the_variables_httpx_reads_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """They are what a connection failure could be blamed on (#69).

    ``WS_PROXY`` is *websockets*' alone and httpx never looks at it, so
    naming it in a message about the requests would send the reader after
    the wrong variable. ``FTP_PROXY`` reaches neither library.
    """
    monkeypatch.setenv("HTTP_PROXY", "http://plain.local:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://secure.local:3128")
    monkeypatch.setenv("WS_PROXY", "http://socket.local:3128")
    monkeypatch.setenv("FTP_PROXY", "http://files.local:3128")
    assert sorted(httpx_environment_proxies()) == ["http", "https"]


def test_no_proxy_variables_at_all_is_an_empty_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which is what lets a message say a proxy is not what went wrong."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("urllib.request.getproxies", dict)
    assert httpx_environment_proxies() == {}


@pytest.mark.parametrize(
    "proxy",
    [
        "http://user:p%40ss@proxy.local:3128",
        "http://us%65r:pass@proxy.local:3128",
    ],
)
def test_proxy_credentials_the_two_would_send_differently_are_refused(
    proxy: str,
) -> None:
    """httpx percent-decodes proxy credentials and websockets does not (#69).

    A password holding a delimiter has to be encoded to appear in a URL at
    all, and then the requests authenticate while the socket collects a 407
    that nothing explains. Both libraries are asked what they would send, so
    only a real disagreement is refused.
    """
    with pytest.raises(ConfigurationError, match="authenticate"):
        resolve_proxy(proxy)


def test_proxy_credentials_the_two_agree_on_pass() -> None:
    """Refusing these would refuse an ordinary authenticated proxy."""
    proxy = "http://user:pass@proxy.local:3128"
    assert resolve_proxy(proxy) == proxy


def test_a_proxy_host_the_two_would_spell_differently_is_refused() -> None:
    """One setting, two hosts, and neither library says a word (#69).

    httpx encodes a host that is not ASCII by UTS 46 and *websockets* by
    IDNA 2003, which part company over the sharp s: the requests would reach
    ``xn--fa-hia.de`` and the socket ``fass.de``.
    """
    with pytest.raises(ConfigurationError, match="punycode"):
        resolve_proxy("http://faß.de:3128")


@pytest.mark.parametrize(
    ("proxy", "why"),
    [
        ("http://münchen.de:3128", "the two encodings agree on this one"),
        ("http://xn--fa-hia.de:3128", "already punycode, so nothing to encode"),
        ("http://[::1]:3128", "an address rather than a name"),
    ],
)
def test_a_proxy_host_the_two_agree_on_passes(proxy: str, why: str) -> None:
    """Refusing every non-ASCII host would refuse working proxies too.

    The two are asked what they would aim at, so only a real disagreement is
    refused, and a name both encode alike is left alone.
    """
    assert resolve_proxy(proxy) == proxy
