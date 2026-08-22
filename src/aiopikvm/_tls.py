"""TLS configuration shared by the HTTP client and the WebSocket.

httpx takes ``verify`` and ``cert`` and works the rest out itself. *websockets*
takes an ``ssl.SSLContext`` and nothing else, so the same settings have to
be turned into one here — otherwise the two halves of this client could end up
trusting different things, which is the sort of difference nobody notices until
it matters.
"""

import os
import ssl

from aiopikvm._exceptions import ConfigurationError

type VerifyTypes = bool | str | ssl.SSLContext
"""What *verify_ssl* accepts, mirroring httpx.

``True``
    Verify against the system trust store.

``False``
    Verify nothing. The default, because PiKVM ships a self-signed
    certificate and refusing it out of the box would make the client
    unusable on an untouched device.

``str``
    Path to a CA bundle, or to a directory of hashed certificates. This is
    the one for a PiKVM re-issued a certificate from a private CA.

`ssl.SSLContext`
    Used as it is, for anything the two above cannot express.
"""

type CertTypes = str | tuple[str, str] | tuple[str, str, str]
"""A client certificate: a combined PEM, or ``(cert, key)``, or
``(cert, key, password)``. Mirrors httpx."""


def build_ssl_context(verify: VerifyTypes, cert: CertTypes | None) -> ssl.SSLContext:
    """Turn *verify* and *cert* into the context a TLS handshake needs.

    Args:
        verify: What to trust; see [`VerifyTypes`][aiopikvm.VerifyTypes].
        cert: Client certificate to present, if any.

    Returns:
        A context configured the way httpx would configure itself from the
        same two arguments.

    Raises:
        ConfigurationError: If the CA path or the certificate cannot be
            loaded, or if a client certificate is asked for alongside a
            ready-made context — that context is the caller's to load it
            into, and doing it here would mean editing an object they own.
    """
    if isinstance(verify, ssl.SSLContext):
        if cert is not None:
            raise ConfigurationError(
                "cert cannot be combined with an ssl.SSLContext: load the "
                "certificate into the context with load_cert_chain() and "
                "pass that, rather than have this client mutate an object "
                "it does not own."
            )
        return verify

    if verify is False:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif verify is True:
        context = ssl.create_default_context()
    else:
        path = os.fspath(verify)
        if not os.path.exists(path):
            raise ConfigurationError(f"No CA bundle at {path!r}")
        try:
            # httpx reads a directory as a hashed CA store and anything else
            # as a bundle file; ssl draws the same distinction.
            if os.path.isdir(path):
                context = ssl.create_default_context(capath=path)
            else:
                context = ssl.create_default_context(cafile=path)
        except (OSError, ssl.SSLError) as exc:
            raise ConfigurationError(
                f"Cannot use {path!r} as a CA bundle: {exc}"
            ) from exc

    if cert is not None:
        certfile, keyfile, password = _unpack_cert(cert)
        try:
            context.load_cert_chain(certfile, keyfile, password)
        except (OSError, ssl.SSLError) as exc:
            raise ConfigurationError(
                f"Cannot load the client certificate {certfile!r}: {exc}"
            ) from exc
    return context


def _unpack_cert(cert: CertTypes) -> tuple[str, str | None, str | None]:
    """Split a certificate argument into what ``load_cert_chain`` takes.

    Args:
        cert: The certificate as httpx spells it.

    Returns:
        ``(certfile, keyfile, password)``, the last two ``None`` when the
        argument did not carry them.

    Raises:
        ConfigurationError: If the tuple is not of length two or three.
    """
    if isinstance(cert, str):
        return (cert, None, None)
    if len(cert) == 2:
        return (cert[0], cert[1], None)
    if len(cert) == 3:
        return (cert[0], cert[1], cert[2])
    raise ConfigurationError(
        f"cert must be a path, a (cert, key) pair or a (cert, key, password) "
        f"triple; got {len(cert)} items"
    )
