"""Connection settings shared by the HTTP client and the WebSocket.

The two transports underneath aiopikvm read the same settings in different
shapes, and disagree about what they accept. httpx takes TLS verification as
``True``, ``False`` or an :class:`ssl.SSLContext`, and still takes a path as
well — deprecated in 0.28, and warned about; *websockets* takes a context,
``True``, or nothing at all for a plain socket, and never took a path.

They disagree about proxies more sharply. *websockets* refuses a URL with no
host, with a path, a query, a fragment, or a username and no password; httpx
takes every one of those, ignoring the parts it has no use for and sending
the lone username as ``username:``. A port above 65535 httpx takes and
*websockets* refuses. For a ``socks5://`` URL with no usable port the two
pick different defaults — 1080 and 80. So one proxy setting could configure
the requests and break the socket, or quietly send the two halves of this
client to two different proxies.

Both settings are therefore reduced here, once, before either library sees
them: a CA path becomes one context object that reaches both transports, and
a proxy is measured against what both of them will take, rather than failing
later as whatever the first library to look at it calls the problem.

Two divergences are left standing, because closing them would mean changing
what a working setting already does. ``verify_ssl=True`` is passed on as it
came, and httpx then verifies against certifi's roots while *websockets*
verifies against the system store; building a context here to settle it
would silently move httpx off the store it has always used. And percent-
encoded proxy credentials httpx decodes before sending, while *websockets*
sends them as written — refusing them would refuse the only way to write a
password containing a delimiter.
"""

import os
import ssl
import urllib.parse
import urllib.request

import httpx
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

_PROXY_ENV_KEYS = ("all", "http", "https", "socks", "ws", "wss")
"""Keys :func:`urllib.request.getproxies` files a proxy under that could be
read by either library.

Not narrowed to the scheme of the device: httpx builds an ``http://`` mount
out of ``HTTP_PROXY`` whatever the base URL is, and a redirect followed down
to ``http://`` would then go through it. Everything else the environment may
hold, ``ftp_proxy`` and the rest, reaches neither library and is left alone.
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
            can raise anything at all from ``__fspath__``. None of them is a
            ``PiKVMError``.
    """
    if isinstance(verify_ssl, bool | ssl.SSLContext):
        return verify_ssl
    try:
        path = os.fspath(verify_ssl)
    except Exception as exc:
        # __fspath__ belongs to whoever wrote the path object and can raise
        # anything at all, TypeError included when what arrived was not a
        # path in the first place. The cause is kept, so nothing is hidden.
        raise ConfigurationError(
            f"Cannot read a path out of {verify_ssl!r}: {exc}"
        ) from exc
    try:
        if os.path.isdir(path):
            return ssl.create_default_context(capath=path)
        return ssl.create_default_context(cafile=path)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"Cannot verify TLS against {verify_ssl!r}: {exc}. The path must "
            "be a PEM bundle, or a directory of them prepared with c_rehash."
        ) from exc


def resolve_proxy(proxy: str | None) -> str | None:
    """Hold an explicit proxy to what *both* transports will do with it.

    It was written for this client, so it has to work for both halves of it:
    a URL only httpx takes would configure the requests and break the
    socket, and one the two would reach on different ports would quietly be
    two proxies rather than one.

    Args:
        proxy: Proxy URL as the caller wrote it, or ``None`` to leave the
            choice to the environment.

    Returns:
        The URL unchanged, or ``None``.

    Raises:
        ConfigurationError: The proxy is one of the two would refuse, or one
            they would not read alike.
    """
    if proxy is None:
        return None
    _refuse_unusable(proxy, f"the proxy {proxy!r}")
    return proxy


def refuse_unusable_environment_proxies(url: str) -> None:
    """Refuse an environment proxy that would escape the error hierarchy.

    Called where the environment is read rather than where the client is
    built, because between those two moments it can change.

    What the environment holds is judged more leniently than what the caller
    passed, since it is shared with every other program on the machine: only
    the port is read, that being the one fault that would otherwise leave
    ``PiKVMError`` altogether — as an ``OverflowError`` from httpx deep
    inside a task group, or from *websockets* as something indistinguishable
    from the connection dropping. The rest of what *websockets* refuses it
    refuses during the handshake, as a ``ConfigurationError`` naming the
    proxy, which is already the right answer in the right place.

    A host ``NO_PROXY`` covers is left alone entirely. Neither library would
    read a proxy for it, so refusing one would fail a client that had
    nothing wrong with it.

    Args:
        url: URL this connection is for, to ask whether the environment
            exempts its host from proxying at all.

    Raises:
        ConfigurationError: A variable either library reads holds a port
            outside 0-65535, one that is not a number, or none at all where
            the two would fill the blank in differently.
    """
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        # A URL neither library will accept either. Whatever it is wrong
        # about, it is not the proxies, so they are still worth checking and
        # the transport gets to report the URL itself.
        host = ""
    if host and urllib.request.proxy_bypass(host):
        return
    for key, env_proxy in environment_proxies().items():
        _refuse_unusable_port(
            # A variable holding no scheme is one httpx fills in, and until
            # it is filled in the port is not in the netloc where urlsplit
            # looks for it.
            env_proxy if "://" in env_proxy else f"http://{env_proxy}",
            f"the {key} proxy {env_proxy!r} the environment sets",
        )


def environment_proxies() -> dict[str, str]:
    """The proxies the environment holds that either library could read.

    Returns:
        The applicable variables, keyed as
        :func:`urllib.request.getproxies` keys them. Empty when the
        environment names none, which is what makes it possible to say that
        a proxy is not what a later failure could be about.
    """
    environment = urllib.request.getproxies()
    return {key: environment[key] for key in _PROXY_ENV_KEYS if key in environment}


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
    # parse_proxy fills a missing port in with its own default, so the raw
    # one has to be read again to see that there was none.
    _refuse_split_socks_port(parsed.scheme, urllib.parse.urlsplit(proxy).port, source)
    _refuse_split_credentials(parsed, proxy, source)


def _refuse_unusable_port(proxy: str, source: str) -> None:
    """Refuse a proxy URL carrying a port nothing could connect to.

    Args:
        proxy: Proxy URL to check, with a scheme.
        source: How to name it in the message.

    Raises:
        ConfigurationError: The port is not a number, is outside 0-65535, or
            is missing from a socks URL.
    """
    split = urllib.parse.urlsplit(proxy)
    try:
        # The port is parsed by the property rather than by urlsplit itself,
        # so reading it is the check.
        port = split.port
    except ValueError as exc:
        raise ConfigurationError(f"Cannot use {source}: {exc}") from exc
    _refuse_split_socks_port(split.scheme, port, source)


def _refuse_split_credentials(
    parsed: websockets.proxy.Proxy, proxy: str, source: str
) -> None:
    """Refuse credentials the two libraries would not send the same way.

    httpx percent-decodes the user information before sending it and
    *websockets* sends it as written, so a password holding a delimiter —
    which has to be encoded to appear in a URL at all — authenticates the
    requests and fails the socket with a 407 nothing explains. Rather than
    decide which spellings diverge, both libraries are asked what they would
    send, and only an actual disagreement is refused.

    Args:
        parsed: What *websockets* made of the URL.
        proxy: The URL itself.
        source: How to name it in the message.

    Raises:
        ConfigurationError: The two would authenticate as different users.
    """
    try:
        by_httpx = httpx.Proxy(url=proxy).auth
    except (ValueError, httpx.InvalidURL):
        # A URL httpx will not have at all, such as a socks4:// one. It
        # refuses it itself, and names the proxy when it does.
        return
    sent_by_httpx: tuple[str | None, str | None] = by_httpx or (None, None)
    if sent_by_httpx != (parsed.username, parsed.password):
        raise ConfigurationError(
            f"Cannot use {source}: the requests would authenticate to it as "
            f"{sent_by_httpx[0]!r} and the WebSocket as {parsed.username!r}, "
            "because httpx percent-decodes proxy credentials and websockets "
            "does not. Use a proxy whose credentials need no encoding."
        )


def _refuse_split_socks_port(scheme: str, port: int | None, source: str) -> None:
    """Refuse a socks proxy the two libraries would reach on different ports.

    Args:
        scheme: Scheme of the proxy URL.
        port: Port it carries, if any. Zero counts as none: both libraries
            treat it as falsy and fall back on their own default.
        source: How to name the proxy in the message.

    Raises:
        ConfigurationError: The two would fill the blank in differently.
    """
    if scheme in _SOCKS_SCHEMES and not port:
        raise ConfigurationError(
            f"Cannot use {source}: a {scheme} proxy needs an explicit port. "
            "Without a usable one the requests would go to port 1080 and the "
            "WebSocket to port 80 — one setting, two different proxies."
        )
