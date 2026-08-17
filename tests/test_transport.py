"""Connection-setting tests — one setting, understood by both transports."""

import os
import ssl
from pathlib import Path

import certifi
import pytest

from aiopikvm import ConfigurationError
from aiopikvm._transport import resolve_proxy, resolve_verify_ssl


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

    def __fspath__(self) -> str:
        raise OSError("the filesystem this path came from is gone")


def test_a_path_object_that_raises_is_a_configuration_error() -> None:
    """``os.fspath`` runs the caller's own code, which can fail.

    A str or a Path never raises here, but the annotation takes any
    ``os.PathLike[str]``, and one of those raising outside the try would
    leave the hierarchy through both public constructors.
    """
    with pytest.raises(ConfigurationError, match="Cannot verify TLS against"):
        resolve_verify_ssl(_UnspeakablePath())


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
    assert resolve_proxy(proxy, "https://pikvm.local", True) == proxy


def test_no_proxy_stays_none() -> None:
    """With nothing passed, the libraries are left to their own defaults."""
    assert resolve_proxy(None, "https://pikvm.local", False) is None


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
        ("http://user@proxy.local", "a username httpx would drop in silence"),
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
        resolve_proxy(proxy, "https://pikvm.local", True)


@pytest.mark.parametrize("proxy", ["socks5://proxy.local", "socks5h://proxy.local"])
def test_a_socks_proxy_without_a_port_is_refused(proxy: str) -> None:
    """The two libraries would fill the blank in differently (#69).

    httpcore reads a portless socks5 URL as port 1080 and *websockets* reads
    it as port 80, so the one setting would reach two different proxies —
    and neither library would say a word about it.
    """
    with pytest.raises(ConfigurationError, match="needs an explicit port"):
        resolve_proxy(proxy, "https://pikvm.local", True)


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
        resolve_proxy(None, "https://pikvm.local", True)


def test_an_environment_proxy_is_left_alone_when_the_environment_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither library will read it, so neither will this."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:99999")
    assert resolve_proxy(None, "https://pikvm.local", False) is None


def test_an_environment_proxy_no_library_would_read_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing it would fail a client that had nothing wrong with it.

    ``ftp_proxy`` reaches neither transport, and ``http_proxy`` reaches
    neither when the device is on https — httpx looks up the target's own
    scheme, and *websockets* looks up ``wss``, ``socks`` and ``https``.
    """
    monkeypatch.setenv("FTP_PROXY", "http://proxy.local:99999")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.local:99999")
    assert resolve_proxy(None, "https://pikvm.local", True) is None


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
    assert resolve_proxy(None, "https://pikvm.local", True) is None


def test_a_scheme_less_environment_proxy_still_has_its_port_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx fills the scheme in, and until it is filled in urlsplit reads
    the host as the scheme and never looks at the port at all."""
    monkeypatch.setenv("HTTPS_PROXY", "proxy.local:99999")
    with pytest.raises(ConfigurationError, match="the https proxy"):
        resolve_proxy(None, "https://pikvm.local", True)
