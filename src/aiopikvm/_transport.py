"""Connection settings shared by the HTTP client and the WebSocket.

The two transports underneath aiopikvm read the same settings in different
shapes, and disagree about what they accept. httpx takes TLS verification as
``True``, ``False`` or an :class:`ssl.SSLContext`, and still takes a path as
well — deprecated in 0.28, and warned about; *websockets* takes a context,
``True``, or nothing at all for a plain socket, and never took a path. A proxy
URL whose port is outside 0-65535 is accepted by one and refused by the other.

So both settings are reduced here, once, before either library sees them: the
same TLS object reaches both, and a proxy either of them would refuse is
refused where the mistake is, rather than as an unrelated-looking failure from
whichever library got to it first.
"""

import os
import ssl
import urllib.parse

from aiopikvm._exceptions import ConfigurationError

type VerifySSL = bool | str | os.PathLike[str] | ssl.SSLContext
"""Every shape TLS verification can be configured in.

``True`` verifies against the default trust store and ``False`` verifies
nothing. A path names a CA bundle — a PEM file, or a directory of them
prepared with ``c_rehash`` — which is what a device behind a private CA
needs. An :class:`ssl.SSLContext` is used exactly as it is, which is also how
a client certificate is supplied, through
:meth:`ssl.SSLContext.load_cert_chain`.
"""


def resolve_verify_ssl(verify_ssl: VerifySSL) -> bool | ssl.SSLContext:
    """Reduce a TLS setting to the shape both transports understand.

    A bundle *file* is read here, so a path naming nothing, or a file holding
    no certificate, fails at once. A *directory* is not read: OpenSSL walks a
    hashed store one certificate per lookup, so an empty or unhashed one is
    only found out about during the handshake.

    Args:
        verify_ssl: TLS verification setting, as the caller wrote it.

    Returns:
        The setting itself when it is already a bool or a context, and a
        context built from the CA bundle when it is a path.

    Raises:
        ConfigurationError: The path is not a CA bundle this machine can
            load. A missing file and one holding no certificate arrive as
            :class:`OSError` subclasses, a path with a null byte in it as a
            :class:`ValueError`, and neither is a ``PiKVMError``.
    """
    if isinstance(verify_ssl, bool | ssl.SSLContext):
        return verify_ssl
    path = os.fspath(verify_ssl)
    try:
        if os.path.isdir(path):
            return ssl.create_default_context(capath=path)
        return ssl.create_default_context(cafile=path)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"Cannot verify TLS against {path!r}: {exc}. The path must be a "
            "PEM bundle, or a directory of them prepared with c_rehash."
        ) from exc


def resolve_proxy(proxy: str | None) -> str | None:
    """Check a proxy URL where the mistake is, rather than at connect time.

    Only the port is checked here, because it is the one part the libraries
    neither agree on nor report usefully. httpx accepts a port outside
    0-65535 and *websockets* refuses it; a port that is not a number at all
    reaches httpx as an ``InvalidURL`` indistinguishable from a bad PiKVM URL
    and *websockets* as a bare ``ValueError`` indistinguishable from the
    connection dropping. Everything else — an unknown scheme, a missing host
    — both of them already refuse in a way that names the proxy.

    Args:
        proxy: Proxy URL as the caller wrote it, or ``None`` for no proxy.

    Returns:
        The URL unchanged, or ``None``.

    Raises:
        ConfigurationError: The URL carries a port neither library can use.
    """
    if proxy is None:
        return None
    try:
        # The port is parsed by the property rather than by urlsplit itself,
        # so reading it is the check; the value is of no use here.
        _ = urllib.parse.urlsplit(proxy).port
    except ValueError as exc:
        raise ConfigurationError(f"Cannot use the proxy {proxy!r}: {exc}") from exc
    return proxy
