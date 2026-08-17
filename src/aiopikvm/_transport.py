"""Connection settings shared by the HTTP client and the WebSocket.

The two transports underneath aiopikvm read the same settings in different
shapes, and disagree about what they accept. httpx takes TLS verification as
``True``, ``False`` or an :class:`ssl.SSLContext`, and still takes a path as
well — deprecated in 0.28, and warned about; *websockets* takes a context,
``True``, or nothing at all for a plain socket, and never took a path.

They disagree about proxies more sharply. *websockets* refuses a URL with no
host, with a path, a query, a fragment, or a username and no password; httpx
takes every one of those, ignoring the parts it has no use for and dropping
the username without a word. A port above 65535 httpx takes and *websockets*
refuses. For a ``socks5://`` URL with no port at all the two pick different
defaults — 1080 and 80. So one proxy setting could configure the requests and
break the socket, or quietly send the two halves of this client to two
different proxies.

Both settings are therefore reduced here, once, before either library sees
them: the same TLS object reaches both transports, and a proxy is measured
against what both of them will take, rather than failing later as whatever
the first library to look at it calls the problem.
"""

import os
import ssl
import urllib.parse
import urllib.request

import websockets.exceptions
import websockets.proxy

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

_SOCKS_SCHEMES = ("socks5", "socks5h")
"""Schemes the two libraries give different default ports to."""

_ENV_PROXY_KEYS_SECURE = ("all", "https", "socks", "wss")
_ENV_PROXY_KEYS_PLAIN = ("all", "http", "https", "socks", "ws")
"""Keys :func:`urllib.request.getproxies` files a usable proxy under.

httpx reads the target's own scheme and ``all``; *websockets* reads the
WebSocket scheme, ``socks``, and — whatever the target scheme — ``https``.
Everything else the environment may hold, ``ftp_proxy`` and the rest, reaches
neither library from here and is left alone.
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
            :class:`ValueError`, and a path object of the caller's own making
            can raise either from ``__fspath__``. None is a ``PiKVMError``.
    """
    if isinstance(verify_ssl, bool | ssl.SSLContext):
        return verify_ssl
    try:
        path = os.fspath(verify_ssl)
        if os.path.isdir(path):
            return ssl.create_default_context(capath=path)
        return ssl.create_default_context(cafile=path)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"Cannot verify TLS against {verify_ssl!r}: {exc}. The path must "
            "be a PEM bundle, or a directory of them prepared with c_rehash."
        ) from exc


def resolve_proxy(proxy: str | None, url: str, trust_env: bool) -> str | None:
    """Refuse a proxy that would not serve both transports alike.

    What the caller passed is held to what *both* libraries take, because it
    was written for this client and has to work for both halves of it.

    What the environment sets is shared with every other program on the
    machine, so only its port is read here — the one part that would
    otherwise leave the ``PiKVMError`` hierarchy altogether, as an
    ``OverflowError`` from httpx deep inside a task group, or arrive from
    *websockets* looking like the connection dropped. The rest of what
    *websockets* refuses in an environment proxy it refuses during the
    handshake, as a ``ConfigurationError``, which is already the right answer
    in the right place.

    Args:
        proxy: Proxy URL as the caller wrote it, or ``None`` to leave the
            choice to the environment.
        url: URL this connection is for. Only the scheme is read, to tell
            which environment variables could apply to it.
        trust_env: Whether the environment is read at all.

    Returns:
        The URL unchanged, or ``None`` when there is no explicit proxy.

    Raises:
        ConfigurationError: The proxy cannot be used, or would not be the
            same proxy for both transports.
    """
    if proxy is not None:
        _refuse_unusable(proxy, f"the proxy {proxy!r}")
        return proxy
    if not trust_env:
        return None
    for key, env_proxy in environment_proxies(url).items():
        _refuse_unusable_port(
            # A variable holding no scheme is one httpx fills in, and until
            # it is filled in the port is not in the netloc where urlsplit
            # looks for it.
            env_proxy if "://" in env_proxy else f"http://{env_proxy}",
            f"the {key} proxy {env_proxy!r} the environment sets",
        )
    return None


def environment_proxies(url: str) -> dict[str, str]:
    """The proxies the environment holds that could carry *url*'s traffic.

    Args:
        url: URL the connection is for. Only the scheme is read.

    Returns:
        The applicable variables, keyed as
        :func:`urllib.request.getproxies` keys them. Empty when the
        environment names none, which is also what makes it possible to say
        that a proxy is not what a later failure could be about.
    """
    secure = url.startswith(("https://", "wss://"))
    keys = _ENV_PROXY_KEYS_SECURE if secure else _ENV_PROXY_KEYS_PLAIN
    environment = urllib.request.getproxies()
    return {key: environment[key] for key in keys if key in environment}


def _refuse_unusable(proxy: str, source: str) -> None:
    """Refuse a proxy URL either library would turn down.

    *websockets* has the stricter parser of the two, so it is the one asked.
    Everything it accepts httpx accepts as well, apart from a ``socks4://``
    scheme, which httpx turns down itself and names the proxy when it does.

    Args:
        proxy: Proxy URL to check.
        source: How to name it in the message, e.g. ``"the proxy 'x'"``.

    Raises:
        ConfigurationError: The URL is one of the two would refuse, or one
            they would read as two different proxies.
    """
    try:
        parsed = websockets.proxy.parse_proxy(proxy)
    except (websockets.exceptions.InvalidProxy, ValueError) as exc:
        # parse_proxy reads the port through urlsplit's property, which
        # raises a bare ValueError rather than its own InvalidProxy.
        raise ConfigurationError(f"Cannot use {source}: {exc}") from exc
    if parsed.scheme in _SOCKS_SCHEMES and urllib.parse.urlsplit(proxy).port is None:
        raise ConfigurationError(
            f"Cannot use {source}: a {parsed.scheme} proxy needs an explicit "
            "port. Without one the requests would go to port 1080 and the "
            "WebSocket to port 80 — one setting, two different proxies."
        )


def _refuse_unusable_port(proxy: str, source: str) -> None:
    """Refuse a proxy URL carrying a port nothing could connect to.

    Args:
        proxy: Proxy URL to check, with a scheme.
        source: How to name it in the message.

    Raises:
        ConfigurationError: The port is not a number, or is outside 0-65535.
    """
    try:
        # The port is parsed by the property rather than by urlsplit itself,
        # so reading it is the check; the value is of no use here.
        _ = urllib.parse.urlsplit(proxy).port
    except ValueError as exc:
        raise ConfigurationError(f"Cannot use {source}: {exc}") from exc
