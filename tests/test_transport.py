"""Connection-setting tests — one setting, understood by both transports."""

import ssl
from pathlib import Path

import certifi
import pytest

from aiopikvm import ConfigurationError
from aiopikvm._transport import resolve_proxy, resolve_verify_ssl


def test_bools_pass_straight_through() -> None:
    """httpx and websockets both read these themselves."""
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


def test_a_proxy_passes_through_and_none_stays_none() -> None:
    """Nothing is rewritten; the URL is only looked at."""
    assert resolve_proxy(None) is None
    assert resolve_proxy("http://proxy.local:3128") == "http://proxy.local:3128"


@pytest.mark.parametrize(
    "proxy",
    [
        "http://proxy.local:notaport",
        "http://proxy.local:99999",
        "http://[::1:3128",
    ],
)
def test_an_unusable_proxy_port_is_a_configuration_error(proxy: str) -> None:
    """The port is the one part the two libraries report unusably (#69).

    httpx accepts 99999 and websockets refuses it; a port that is not a
    number reaches httpx as an ``InvalidURL`` that reads like a bad PiKVM
    URL, and websockets as a bare ``ValueError`` that reads like the
    connection dropping.
    """
    with pytest.raises(ConfigurationError, match="Cannot use the proxy"):
        resolve_proxy(proxy)


@pytest.mark.parametrize("proxy", ["proxy.local:3128", "", "ftp://proxy.local:1"])
def test_a_proxy_scheme_is_left_to_the_libraries(proxy: str) -> None:
    """Both of them already refuse these in a way that names the proxy."""
    assert resolve_proxy(proxy) == proxy
