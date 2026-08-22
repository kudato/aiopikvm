"""TLS and proxy configuration.

The point of these is that both halves of the client end up trusting the same
thing: httpx works an `SSLContext` out for itself from `verify`/`cert`, while
*websockets* takes a context and nothing else, so the two are built from one
place and both are checked here.
"""

import ssl
from pathlib import Path

import pytest

from aiopikvm import CertTypes, ConfigurationError, PiKVM
from aiopikvm._tls import build_ssl_context

TLS_DIR = Path(__file__).parent / "fixtures" / "tls"
CRT = str(TLS_DIR / "client.crt")
KEY = str(TLS_DIR / "client.key")

URL = "https://pikvm.local"


def test_verification_off_trusts_anything() -> None:
    # The default, and the only thing that works on an untouched PiKVM.
    context = build_ssl_context(False, None)
    assert context.verify_mode is ssl.CERT_NONE
    assert context.check_hostname is False


def test_verification_on_uses_the_system_store() -> None:
    context = build_ssl_context(True, None)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_a_ca_bundle_is_the_only_thing_trusted() -> None:
    # The private-CA case the issue is about: a PiKVM re-issued a
    # certificate has one trust anchor, not the system store's hundreds.
    context = build_ssl_context(CRT, None)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert len(context.get_ca_certs()) == 1


def test_a_directory_is_read_as_a_hashed_store() -> None:
    # httpx reads a directory as capath rather than cafile, and so does this.
    # An empty one loads: OpenSSL looks inside only when it verifies.
    context = build_ssl_context(str(TLS_DIR), None)
    assert context.verify_mode is ssl.CERT_REQUIRED


def test_a_context_is_used_as_it_is() -> None:
    given = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert build_ssl_context(given, None) is given


@pytest.mark.parametrize("with_key", [False, True])
def test_a_client_certificate_is_loaded_as_a_pair(with_key: bool) -> None:
    cert: CertTypes = (CRT, KEY, "") if with_key else (CRT, KEY)
    assert build_ssl_context(False, cert) is not None


def test_a_combined_pem_is_loaded_as_one_file(tmp_path: Path) -> None:
    # httpx's third spelling: certificate and key in the same file.
    combined = tmp_path / "combined.pem"
    combined.write_text(Path(CRT).read_text() + Path(KEY).read_text())
    assert build_ssl_context(False, str(combined)) is not None


def test_a_certificate_beside_a_context_is_refused() -> None:
    # Silently ignoring it would be worse, and loading it would mean editing
    # an object the caller owns.
    given = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with pytest.raises(ConfigurationError, match="cannot be combined"):
        build_ssl_context(given, CRT)


def test_a_missing_ca_bundle_says_so() -> None:
    with pytest.raises(ConfigurationError, match="No CA bundle at"):
        build_ssl_context("/nowhere/ca.pem", None)


def test_a_ca_bundle_that_is_not_one_says_so(tmp_path: Path) -> None:
    junk = tmp_path / "ca.pem"
    junk.write_text("not a certificate")
    with pytest.raises(ConfigurationError, match="as a CA bundle"):
        build_ssl_context(str(junk), None)


def test_a_client_certificate_that_is_not_one_says_so(tmp_path: Path) -> None:
    junk = tmp_path / "client.pem"
    junk.write_text("not a certificate")
    with pytest.raises(ConfigurationError, match="client certificate"):
        build_ssl_context(False, str(junk))


def test_an_unusable_certificate_tuple_says_so() -> None:
    with pytest.raises(ConfigurationError, match="got 4 items"):
        build_ssl_context(False, (CRT, KEY, "", "extra"))  # type: ignore[arg-type]


async def test_the_client_passes_the_settings_to_httpx(
    recwarn: pytest.WarningsRecorder,
) -> None:
    async with PiKVM(URL, verify_ssl=CRT, proxy="http://proxy:3128") as kvm:
        assert kvm._client is not None
        # httpx keeps trust_env on the client; the rest is inside its
        # transport, so this is as far as an assertion can reach.
        assert kvm._client.trust_env is True

    async with PiKVM(URL, trust_env=False) as kvm:
        assert kvm._client is not None
        assert kvm._client.trust_env is False

    # httpx 0.28 deprecates both `cert=` and `verify=<path>`; the context is
    # built here and handed over ready, so neither is ever used.
    assert [w for w in recwarn if issubclass(w.category, DeprecationWarning)] == []


async def test_the_websocket_gets_the_same_trust() -> None:
    # The whole reason the context is built in one place: a socket that
    # trusts more than the REST client would be a hole nobody looks at.
    async with PiKVM(URL, verify_ssl=CRT) as kvm:
        context = build_ssl_context(kvm.ws()._verify_ssl, kvm.ws()._cert)
        assert len(context.get_ca_certs()) == 1


async def test_the_websocket_proxy_follows_trust_env() -> None:
    async with PiKVM(URL, trust_env=False) as kvm:
        assert kvm.ws()._trust_env is False
    async with PiKVM(URL, proxy="http://proxy:3128") as kvm:
        assert kvm.ws()._proxy == "http://proxy:3128"
