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
differently, httpx by IDNA 2008 and *websockets* by IDNA 2003, so ``faß.de``
is ``xn--fa-hia.de`` for one and ``fass.de`` for the other — and ``☃.net``
httpx will not encode at all while *websockets* is happy to. ``socks4://``
and ``socks4a://`` *websockets* 16 speaks and httpx has no transport for at
all. And credentials httpx percent-decodes before sending, while *websockets*
sends them as written.

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

These divergences are left standing, because closing them would mean changing
what a working setting already does:

* ``verify_ssl=True`` is passed on as it came, and each library then builds
  its own context. httpx reads ``SSL_CERT_FILE``, or ``SSL_CERT_DIR`` when
  the first is unset, and certifi's roots when neither is — one source, never
  both, and only while *trust_env* is on. *websockets* asks
  :func:`ssl.create_default_context`, which loads OpenSSL's default paths:
  a file and a hashed directory, filled in independently, each from its
  variable where that is set and from OpenSSL's own compiled-in path where it
  is not. So setting one variable narrows httpx to what it names and leaves
  the socket trusting that *and* whatever the build's other default path
  holds — which is a full system store on one machine and nothing at all on
  another. *trust_env* is httpx's idea and OpenSSL has never heard of it.
  The two therefore verify against the same certificates only by accident,
  and building a context here to settle it would silently move httpx off the
  roots it has always used.
* An ``https://`` proxy is not verified by *verify_ssl*: a context passed for
  the device reaches the device, not the proxy in front of it. httpx leaves
  that leg to httpcore's own default, :func:`ssl.create_default_context` with
  certifi loaded on top, and *websockets* to ``proxy_ssl``, which stays at
  ``True`` and has asyncio build the same context without the certifi part.
  Both legs therefore read ``SSL_CERT_FILE`` and ``SSL_CERT_DIR``, whatever
  *trust_env* says, since it is OpenSSL that reads them and not httpx.
* A ``socks5://`` proxy resolves the device's name through the proxy for the
  requests and on this machine for the socket, httpcore sending the name
  itself and *websockets* asking for ``socks5`` without remote resolution.
  ``socks5h://`` is the spelling both resolve remotely.
* Anything the environment sets, as above.
"""

import os
import re
import ssl
import urllib.parse
import urllib.request
from collections.abc import Iterable

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

_REMOVED_BY_URLSPLIT = "\t\r\n"
"""What :func:`urllib.parse.urlsplit` deletes from a URL before parsing it.

*websockets* parses a proxy with :func:`urllib.parse.urlparse`, which is
urlsplit underneath, so what it reads and quotes back is the URL with these
gone. httpx has a parser of its own and refuses a URL holding one outright,
quoting it as it was written. A password written with any of them therefore
reaches a message in either spelling, and the setting itself is quoted here in
the second.
"""

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
            f"Cannot read a path out of {_named(verify_ssl)}: {_said(exc)}"
        ) from exc
    try:
        if os.path.isdir(path):
            return ssl.create_default_context(capath=path)
        return ssl.create_default_context(cafile=path)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"Cannot verify TLS against {_named(verify_ssl)}: {exc}. The path "
            "must be a PEM bundle, or a directory of them prepared with "
            "c_rehash."
        ) from exc


def _named(value: object) -> str:
    """Put a value into a message without trusting its ``__repr__``.

    The same argument that makes ``__fspath__`` worth guarding against makes
    ``__repr__`` worth it: both belong to whoever wrote the object, and a
    message built from one that raises would leave the hierarchy through the
    clause meant to keep it inside.

    Args:
        value: Whatever the caller passed.

    Returns:
        Its repr, or a name for its type when the repr raises.
    """
    try:
        return repr(value)
    except Exception:
        return f"a {type(value).__name__} whose repr raises"


def _said(exc: BaseException) -> str:
    """Quote an exception that came from the caller's own code.

    ``__fspath__`` can raise anything, and what it raises can carry a
    ``__str__`` of the same making. Reading the message is therefore the last
    step that can still fail, and failing there would let the original
    exception out in place of the ``ConfigurationError`` built around it.

    Args:
        exc: The exception to quote.

    Returns:
        What it says, or a name for its type when saying it raises.
    """
    try:
        return str(exc)
    except Exception:
        return f"a {type(exc).__name__} whose message raises"


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
    _refuse_unusable(proxy, f"the proxy {mask_proxy(proxy)!r}")
    return proxy


def mask_proxy(proxy: str) -> str:
    """Hide the password in a proxy URL on its way into a message.

    An exception carries further than the setting it came from — into logs,
    into a bug report — and the password is the one part of the URL that
    identifies nothing.

    Args:
        proxy: Proxy URL as it was written.

    Returns:
        The URL with its password replaced, or unchanged when it has none or
        holds nothing a password could be read out of.
    """
    return _without_password(proxy, proxy)


def _without_password(text: str, proxy: str) -> str:
    """Take the proxy's password out of whatever is about to be reported.

    Both libraries quote the URL they were given in the exceptions they
    raise, credentials and all, and those exceptions are quoted in turn here.
    So the message is scrubbed rather than only the part of it built here.

    Args:
        text: What is about to be reported.
        proxy: The proxy URL whose password is not to appear in it.

    Returns:
        The text, with any occurrence of the password replaced.
    """
    for password in _passwords_in(proxy):
        text = _however_written(password).sub(":***@", text)
    return text


def _however_written(password: str) -> re.Pattern[str]:
    """Match a password wherever the characters urlsplit drops were written.

    A password read after those characters were dropped is not the string the
    message carries: ``s3c\\nret`` as written is ``s3cret`` once urlsplit has
    been through it, and replacing the second spelling leaves the first
    standing. The two differ only by characters urlsplit deletes, so the
    pattern allows them anywhere.

    Args:
        password: The password in one of its spellings.

    Returns:
        A pattern matching the userinfo separator, that password however it
        was spelled, and the ``@`` that closes it.
    """
    anywhere = f"[{re.escape(_REMOVED_BY_URLSPLIT)}]*"
    spelt = anywhere.join(re.escape(char) for char in password)
    return re.compile(f":{anywhere}{spelt}{anywhere}@")


def _passwords_in(proxy: str) -> set[str]:
    """Every spelling of the proxy's password a message could carry.

    The password is read out of the URL by hand rather than by
    :func:`urllib.parse.urlsplit`, which refuses a malformed netloc outright
    — an unclosed ``[::1`` among them — and left the password standing in the
    message that was refusing it. What is left is the same rule urlsplit
    applies, up to the last ``@`` of the authority and past the first ``:``,
    and it cannot fail.

    It is applied twice: to the URL as written, and to the URL with the
    characters urlsplit deletes taken out of it. A tab or a newline written
    into the ``//`` that opens the authority hides the password from the
    first reading — there is no ``//`` left to find a netloc after — and
    urlsplit, which is asked as well, answers with nothing at all for a
    netloc it refuses outright, however it was spelled.

    Args:
        proxy: Proxy URL as it was written.

    Returns:
        The password in every form it could appear in, empty when the URL
        carries none.
    """
    found: set[str] = set()
    for text in (proxy, _as_urlsplit_reads(proxy)):
        netloc = re.split(r"[/?#]", text.partition("//")[2], maxsplit=1)[0]
        userinfo, at, _ = netloc.rpartition("@")
        if at:
            _, colon, written = userinfo.partition(":")
            if colon and written:
                found.add(written)
    try:
        parsed = urllib.parse.urlsplit(proxy).password
    except ValueError:
        return found
    if parsed:
        found.add(parsed)
    return found


def _as_urlsplit_reads(proxy: str) -> str:
    """The URL with the characters urlsplit drops taken out of it.

    Args:
        proxy: Proxy URL as it was written.

    Returns:
        The same URL as every parser downstream of urlsplit sees it.
    """
    for char in _REMOVED_BY_URLSPLIT:
        proxy = proxy.replace(char, "")
    return proxy


def httpx_environment_proxies() -> dict[str, str]:
    """The proxies the environment holds that httpx could read.

    Whether it reads any of them for a given URL is its own business, decided
    by mounts and ``NO_PROXY`` rules this package does not reproduce. What
    this answers is the weaker question worth answering: whether a proxy is
    something a failure could be about at all.

    Returns:
        The applicable settings, keyed as
        :func:`urllib.request.getproxies` keys them, and empty when this
        machine names none.
    """
    settings = urllib.request.getproxies()
    return {key: settings[key] for key in _HTTPX_PROXY_ENV_KEYS if key in settings}


def proxy_environment_variables(keys: Iterable[str]) -> list[str]:
    """Name the variables that set a proxy for *keys*, as they are spelled.

    ``urllib`` files a proxy under the lowercase of whatever the variable's
    name has before ``_proxy``, and reads every spelling of it:
    ``https_proxy``, ``HTTPS_PROXY`` and ``Https_Proxy`` all reach the
    ``https`` key, and where more than one is set the all-lowercase one wins.
    Uppercasing the key to name the variable would send the reader after a
    variable nobody set, so what is named here is what the environment
    actually spells.

    An empty answer for a key that :func:`httpx_environment_proxies` did find
    means the setting is not the environment's at all:
    ``urllib.request.getproxies`` falls back on what macOS and Windows have
    configured system-wide, and httpx reads it that way too.

    Args:
        keys: Proxy keys to look for, as
            :func:`urllib.request.getproxies` keys them.

    Returns:
        The names of the variables that set one, sorted, and empty when the
        environment names none of them.
    """
    wanted = set(keys)
    return sorted(
        name
        for name, value in os.environ.items()
        if value and _proxy_key(name) in wanted
    )


def _proxy_key(name: str) -> str:
    """The key ``urllib`` would file an environment variable's proxy under.

    Args:
        name: Environment variable name, as the environment spells it.

    Returns:
        The lowercase scheme it sets a proxy for, or ``""`` when it sets
        none.
    """
    lowered = name.lower()
    if len(name) > 5 and lowered.endswith("_proxy"):
        return lowered[:-6]
    return ""


def _refuse_unusable(proxy: str, source: str) -> None:
    """Refuse a proxy URL either library would turn down or read differently.

    *websockets* has the stricter parser of the two on most counts, so it is
    asked first; where httpx is the stricter one — a ``socks4://`` scheme, a
    host it will not encode — asking it is what the checks below are for.

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
        raise ConfigurationError(
            f"Cannot use {source}: {_without_password(str(exc), proxy)}"
        ) from exc
    # parse_proxy fills a missing port in with its own default, so the raw
    # one has to be read again to see that there was none.
    _refuse_split_socks_port(parsed.scheme, urllib.parse.urlsplit(proxy).port, source)
    _refuse_split_target(parsed, proxy, source)


def _refuse_split_target(
    parsed: websockets.proxy.Proxy, proxy: str, source: str
) -> None:
    """Refuse a proxy the two libraries would not reach the same way.

    They spell a non-ASCII host differently — httpx by IDNA 2008 and
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
    except ValueError as exc:
        # A scheme httpx has no transport for. *websockets* 16 speaks socks4
        # and socks4a and httpx speaks neither, so the socket would connect
        # through the proxy and the requests would not go out at all.
        raise ConfigurationError(
            f"Cannot use {source}: {_without_password(str(exc), proxy)}. The "
            "WebSocket would connect through it and the requests could not. "
            "socks5:// and socks5h:// are the SOCKS spellings both speak."
        ) from exc
    except httpx.InvalidURL as exc:
        # A host httpx will not encode and *websockets* will, ``☃.net`` among
        # them. Left to httpx this surfaces while the client is being built,
        # where nothing knows to blame the proxy rather than the PiKVM URL.
        raise ConfigurationError(
            f"Cannot use {source}: {_without_password(str(exc), proxy)}"
        ) from exc
    host_by_httpx = by_httpx.url.raw_host.decode()
    if host_by_httpx != parsed.host:
        raise ConfigurationError(
            f"Cannot use {source}: the requests would go to {host_by_httpx!r} "
            f"and the WebSocket to {parsed.host!r}, because the two encode a "
            "host that is not ASCII by different rules. Write the host in the "
            "punycode form both of them read alike."
        )
    sent_by_httpx: tuple[str | None, str | None] = by_httpx.auth or (None, None)
    by_websockets = (parsed.username, parsed.password)
    if sent_by_httpx != by_websockets:
        # Naming the username on both sides would print the same name twice
        # whenever it is the password that differs, and read as a message
        # contradicting itself.
        differs = "user name" if sent_by_httpx[0] != by_websockets[0] else "password"
        told = (
            f"as {sent_by_httpx[0]!r} and the WebSocket as {by_websockets[0]!r}"
            if differs == "user name"
            else "with one password and the WebSocket with another"
        )
        raise ConfigurationError(
            f"Cannot use {source}: the requests would authenticate to it "
            f"{told}, because httpx percent-decodes proxy credentials and "
            f"websockets does not, and this {differs} is written with an "
            "escape. Use a proxy whose credentials need no encoding."
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
