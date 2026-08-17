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
pick different defaults — 1080 and 80. A host that is not ASCII they spell
differently, httpx by UTS 46 and *websockets* by IDNA 2003, so ``faß.de`` is
``xn--fa-hia.de`` for one and ``fass.de`` for the other. And credentials
httpx percent-decodes before sending, while *websockets* sends them as
written.

So one proxy setting could configure the requests and break the socket, or
quietly send the two halves of this client to two different proxies. What is
reduced here is therefore what the *caller* passes: a CA path becomes one
context object that reaches both transports, and a proxy is measured against
what both libraries would take and refused when the two would not use it
alike.

What the *environment* names is left to the libraries themselves. Deciding
which variable each of them reads for a given URL, and whether ``NO_PROXY``
exempts it, means reimplementing two sets of rules that disagree: for a host
of ``example.invalid`` an entry of ``.example.invalid`` exempts the socket
and not the requests, ``WS_PROXY`` is read for a plain socket and never by
httpx, and ``urllib``'s own answer — the one *websockets* asks — differs from
both unless it is given the port as well. A guess that lands wrong either
refuses a setting that was working or misses one that was not, so no guess is
made: the libraries read the environment as they always have, and this
package's part is to keep what they raise about it inside ``PiKVMError``.

Three divergences are left standing, because closing them would mean changing
what a working setting already does:

* ``verify_ssl=True`` is passed on as it came. httpx then verifies against
  certifi's roots — or against ``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` when the
  environment names them and is trusted — and *websockets* against the system
  store. Building a context here to settle it would silently move httpx off
  the roots it has always used.
* A ``socks5://`` proxy resolves the device's name through the proxy for the
  requests and on this machine for the socket, httpcore sending the name
  itself and *websockets* asking for ``socks5`` without remote resolution.
  ``socks5h://`` is the spelling both resolve remotely.
* Anything the environment sets, as above.
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

_HTTPX_PROXY_ENV_KEYS = ("all", "http", "https")
"""Keys :func:`urllib.request.getproxies` files a proxy under that httpx reads.

It builds an ``http://``, an ``https://`` and an ``all://`` mount out of them
whatever the base URL's scheme is. The ``ws``, ``wss`` and ``socks`` keys are
*websockets*' business alone, and the rest — ``ftp`` and whatever else the
machine holds — reach neither library.
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
    a URL only httpx takes would configure the requests and break the socket,
    and one the two would not read alike would quietly be two proxies rather
    than one.

    Args:
        proxy: Proxy URL as the caller wrote it, or ``None`` to leave the
            choice to the environment, which is left to the libraries.

    Returns:
        The URL unchanged, or ``None``.

    Raises:
        ConfigurationError: The proxy is one either library would refuse, or
            one they would not use alike.
    """
    if proxy is None:
        return None
    _refuse_unusable(proxy, f"the proxy {proxy!r}")
    return proxy


def httpx_environment_proxies() -> dict[str, str]:
    """The proxies the environment holds that httpx could read.

    Whether it reads any of them for a given URL is its own business, decided
    by mounts and ``NO_PROXY`` rules this package does not reproduce. What
    this answers is the weaker question worth answering: whether a proxy is
    something a failure could be about at all.

    Returns:
        The applicable variables, keyed as
        :func:`urllib.request.getproxies` keys them, and empty when the
        environment names none.
    """
    environment = urllib.request.getproxies()
    return {
        key: environment[key] for key in _HTTPX_PROXY_ENV_KEYS if key in environment
    }


def _refuse_unusable(proxy: str, source: str) -> None:
    """Refuse a proxy URL either library would turn down or read differently.

    *websockets* has the stricter parser of the two, so it is the one asked.
    Everything it accepts httpx accepts as well, apart from a ``socks4://``
    scheme, which httpx turns down itself and names the proxy when it does.

    Args:
        proxy: Proxy URL to check.
        source: How to name it in the message, e.g. ``"the proxy 'x'"``.

    Raises:
        ConfigurationError: The URL is one either library would refuse, or
            one they would read as two different proxies.
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
    _refuse_split_target(parsed, proxy, source)


def _refuse_split_target(
    parsed: websockets.proxy.Proxy, proxy: str, source: str
) -> None:
    """Refuse a proxy the two libraries would not reach the same way.

    They spell a non-ASCII host differently — httpx by UTS 46 and
    *websockets* by IDNA 2003, which agree on ``münchen.de`` and part company
    over ``faß.de`` — and httpx percent-decodes the user information before
    sending it while *websockets* sends it as written, so a password holding
    a delimiter, which has to be encoded to appear in a URL at all,
    authenticates the requests and collects a 407 on the socket that nothing
    explains.

    Rather than decide which spellings diverge, both libraries are asked what
    they would aim at, and only an actual disagreement is refused.

    Args:
        parsed: What *websockets* made of the URL.
        proxy: The URL itself.
        source: How to name it in the message.

    Raises:
        ConfigurationError: The two would reach different hosts, or
            authenticate as different users.
    """
    try:
        by_httpx = httpx.Proxy(url=proxy)
    except (ValueError, httpx.InvalidURL):
        # A URL httpx will not have at all, such as a socks4:// one. It
        # refuses it itself, and names the proxy when it does.
        return
    host_by_httpx = by_httpx.url.raw_host.decode()
    if host_by_httpx != parsed.host:
        raise ConfigurationError(
            f"Cannot use {source}: the requests would go to {host_by_httpx!r} "
            f"and the WebSocket to {parsed.host!r}, because the two encode a "
            "host that is not ASCII by different rules. Write the host in the "
            "punycode form both of them read alike."
        )
    sent_by_httpx: tuple[str | None, str | None] = by_httpx.auth or (None, None)
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
