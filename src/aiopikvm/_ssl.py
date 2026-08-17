"""TLS configuration shared by the HTTP client and the WebSocket.

The two transports underneath aiopikvm read TLS settings in different shapes.
httpx takes ``True``, ``False`` or an :class:`ssl.SSLContext`; *websockets*
takes a context, ``True``, or nothing at all for a plain socket. Neither of
them takes a path to a CA bundle any more — httpx deprecated it in 0.28 and
*websockets* never had it — so a path is turned into a context here, once, and
both of them are handed the same object. Doing it in either transport instead
would leave the other one verifying against something else.
"""

import os
import ssl

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

    Args:
        verify_ssl: TLS verification setting, as the caller wrote it.

    Returns:
        The setting itself when it is already a bool or a context, and a
        context built from the CA bundle when it is a path.

    Raises:
        ConfigurationError: The path is not a CA bundle this machine can
            load. A missing file, an unreadable one and one holding no
            certificate all arrive as :class:`OSError` subclasses.
    """
    if isinstance(verify_ssl, bool | ssl.SSLContext):
        return verify_ssl
    path = os.fspath(verify_ssl)
    try:
        if os.path.isdir(path):
            return ssl.create_default_context(capath=path)
        return ssl.create_default_context(cafile=path)
    except OSError as exc:
        raise ConfigurationError(
            f"Cannot verify TLS against {path!r}: {exc}. The path must be a "
            "PEM bundle, or a directory of them prepared with c_rehash."
        ) from exc
