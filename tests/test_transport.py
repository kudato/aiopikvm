"""Connection-setting tests — one setting, understood by both transports."""

import os
import ssl
from pathlib import Path

import certifi
import pytest

from aiopikvm import ConfigurationError
from aiopikvm._transport import (
    refuse_unusable_environment_proxies,
    resolve_proxy,
    resolve_verify_ssl,
)


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


def test_an_environment_proxy_with_an_unusable_port_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise it leaves the hierarchy from httpx as an OverflowError.

    A port above 65535 is one httpx builds a client with and only fails on
    at connect time, inside a task group, as an ``ExceptionGroup`` no clause
    in this package catches.
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:99999")
    with pytest.raises(ConfigurationError, match="the https proxy"):
        refuse_unusable_environment_proxies("https://pikvm.local")


def test_an_environment_proxy_no_library_would_read_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing it would fail a client that had nothing wrong with it.

    ``ftp_proxy`` reaches neither transport, whatever the device's scheme.
    """
    monkeypatch.setenv("FTP_PROXY", "http://proxy.local:99999")
    refuse_unusable_environment_proxies("https://pikvm.local")


def test_a_plain_http_environment_proxy_is_read_even_behind_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx mounts HTTP_PROXY whatever the base URL's scheme is (#69).

    A redirect followed down to ``http://`` would then go through it, so
    narrowing the check to the device's own scheme would leave the same
    ``OverflowError`` reachable by a longer road.
    """
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.local:99999")
    with pytest.raises(ConfigurationError, match="the http proxy"):
        refuse_unusable_environment_proxies("https://pikvm.local")


def test_a_portless_socks_proxy_in_the_environment_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 1080-against-80 split is a fault of the port, so it is checked
    here too — the two would reach different proxies in silence (#69)."""
    monkeypatch.setenv("HTTPS_PROXY", "socks5://proxy.local")
    with pytest.raises(ConfigurationError, match="needs an explicit port"):
        refuse_unusable_environment_proxies("https://pikvm.local")


def test_an_environment_proxy_keeps_the_rest_of_its_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is shared with every other program on the machine, so only the
    port — the part that would escape the hierarchy — is read here.

    A path is something httpx ignores and *websockets* refuses during the
    handshake, as a ``ConfigurationError`` that names the proxy. That is
    already the right answer in the right place.
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:3128/path")
    refuse_unusable_environment_proxies("https://pikvm.local")


def test_a_scheme_less_environment_proxy_still_has_its_port_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx fills the scheme in, and until it is filled in urlsplit reads
    the host as the scheme and never looks at the port at all."""
    monkeypatch.setenv("HTTPS_PROXY", "proxy.local:99999")
    with pytest.raises(ConfigurationError, match="the https proxy"):
        refuse_unusable_environment_proxies("https://pikvm.local")


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


def test_an_environment_proxy_is_left_alone_for_a_host_no_proxy_covers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither library would use it, so refusing it would break a caller.

    That is what requirement 9 asks for: the defaults leave an existing
    caller with what they had. With the host exempted, a broken variable
    beside it was never going to be read (#69).
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:99999")
    monkeypatch.setenv("NO_PROXY", "pikvm.local")
    refuse_unusable_environment_proxies("https://pikvm.local")

    monkeypatch.setenv("NO_PROXY", "somewhere.else")
    with pytest.raises(ConfigurationError, match="the https proxy"):
        refuse_unusable_environment_proxies("https://pikvm.local")


def test_an_unparsable_url_does_not_stop_the_environment_being_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking about the bypass means parsing the URL, which can fail.

    Whatever such a URL is wrong about, it is not the proxies, and the
    transport reports the URL itself soon enough.
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:99999")
    with pytest.raises(ConfigurationError, match="the https proxy"):
        refuse_unusable_environment_proxies("http://[::1")
