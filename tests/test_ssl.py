"""TLS configuration tests — one setting, understood by both transports."""

import ssl
from pathlib import Path

import certifi
import pytest

from aiopikvm import ConfigurationError
from aiopikvm._ssl import resolve_verify_ssl


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
    not silently added to it.
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
