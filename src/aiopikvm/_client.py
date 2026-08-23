"""PiKVM async client."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

import httpx

from aiopikvm._constants import (
    DEFAULT_AUTH,
    DEFAULT_FOLLOW_REDIRECTS,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    AuthMode,
)
from aiopikvm._exceptions import (
    AuthError,
    ConfigurationError,
    ConnectError,
    ConnectionTimeoutError,
    PiKVMError,
    RedirectError,
    ResponseError,
    _error_fields,
    _status_error,
)
from aiopikvm._media_ws import MediaWebSocket
from aiopikvm._tls import CertTypes, VerifyTypes, build_ssl_context
from aiopikvm._webrtc import (
    _FRAME_BUFFER,
    _KEEPALIVE_INTERVAL,
    _NEGOTIATE_TIMEOUT,
    WebRTCSession,
)
from aiopikvm._ws import (
    _WS_MAX_QUEUE,
    _WS_MAX_SIZE,
    _WS_PING_INTERVAL,
    _WS_PING_TIMEOUT,
    PiKVMWebSocket,
)

if TYPE_CHECKING:
    from types import TracebackType

    from aiopikvm.resources.atx import ATXResource
    from aiopikvm.resources.auth import AuthResource
    from aiopikvm.resources.gpio import GPIOResource
    from aiopikvm.resources.hid import HIDResource
    from aiopikvm.resources.media import MediaResource
    from aiopikvm.resources.msd import MSDResource
    from aiopikvm.resources.prometheus import PrometheusResource
    from aiopikvm.resources.redfish import RedfishResource
    from aiopikvm.resources.streamer import StreamerResource
    from aiopikvm.resources.switch import SwitchResource
    from aiopikvm.resources.system import SystemResource

_COOKIE = "auth_token"
"""Name of the cookie kvmd stores its session token in."""


@contextmanager
def _httpx_errors_translated() -> Iterator[None]:
    """Report httpx's failures as this library's own.

    Both halves of the client send through httpx and have the same set of
    failures to rename, so the mapping lives here rather than in two copies
    that drift.

    Yields:
        Nothing. The block runs, and whatever httpx raises out of it comes
        back as a [`PiKVMError`][aiopikvm.PiKVMError].

    Raises:
        ConnectionTimeoutError: The request timed out.
        RedirectError: The redirects formed a loop, which only happens with
            ``follow_redirects=True``.
        ResponseError: The body did not survive its ``Content-Encoding``.
        ConfigurationError: The URL has no usable scheme.
        ConnectError: Anything else that went wrong on the wire.
    """
    try:
        yield
    except httpx.TimeoutException as exc:
        raise ConnectionTimeoutError(str(exc)) from exc
    except httpx.TooManyRedirects as exc:
        # httpx derives this from RequestError rather than TransportError, so
        # the last clause does not cover it and it would escape PiKVMError.
        raise RedirectError(
            f"{exc} Point the client at the URL the redirects lead to."
        ) from exc
    except httpx.DecodingError as exc:
        # The same again — and reporting it as a connection failure would
        # point at the wrong thing. The bytes arrived; they did not
        # decompress. kvmd's nginx gzips text, so a lossy link makes this
        # reachable on /api/log and on any JSON envelope.
        raise ResponseError(
            f"{exc} The response arrived but did not survive its "
            "Content-Encoding, so there is nothing in it to read."
        ) from exc
    except httpx.UnsupportedProtocol as exc:
        raise ConfigurationError(
            f"{exc} Pass the scheme in the PiKVM URL, e.g. https://pikvm.local."
        ) from exc
    except httpx.TransportError as exc:
        # Covers ConnectError, ReadError/WriteError and the
        # RemoteProtocolError kvmd raises at every restart, which drops
        # in-flight connections after a one-second shutdown timeout.
        raise ConnectError(str(exc)) from exc


class PiKVM:
    """Async client for PiKVM API.

    Usage:

        async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
            await kvm.atx.power_on()

    An external *httpx.AsyncClient* can be provided via *http_client*; in that
    case the caller is responsible for closing it.

    The lifecycle follows the one *httpx.AsyncClient* has, so that wrapping
    one does not change the rules: a client is used once and then closed.
    [`aclose()`][aiopikvm.PiKVM.aclose] — which ``async with`` calls on the
    way out — releases the resources and leaves the object closed for good,
    whether the underlying HTTP client was built here or handed in.

    Reopening and nesting both raise
    [`ConfigurationError`][aiopikvm.ConfigurationError]. Reopening used to
    build a second connection pool under the same object, rereading the
    credentials as they stood at that moment; nesting used to leave the inner
    block's exit closing the connection the outer one was still using.
    """

    def __init__(
        self,
        url: str,
        *,
        user: str = "admin",
        passwd: str = "",
        totp: str | Callable[[], str] | None = None,
        auth: AuthMode = DEFAULT_AUTH,
        session_expire: int = 0,
        verify_ssl: VerifyTypes = DEFAULT_VERIFY_SSL,
        cert: CertTypes | None = None,
        proxy: str | None = None,
        trust_env: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        follow_redirects: bool = DEFAULT_FOLLOW_REDIRECTS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client.

        Args:
            url: PiKVM base URL, including the scheme.
            user: kvmd user name.
            passwd: kvmd password.
            totp: TOTP code, appended to the password. A string is used
                as given, which is good for the one window it belongs to;
                pass a zero-argument callable — [`TOTP`][aiopikvm.TOTP]
                is one — for a client that outlives a code.
            auth: Which credential to send; see
                [`AuthMode`][aiopikvm.AuthMode]. ``"cookie"`` logs in on the
                first request that needs it and again if the session is
                refused, so *user* and *passwd* are still required.
            session_expire: Lifetime, in seconds, of a session opened that
                way. ``0`` asks kvmd for an unlimited one, which is its own
                default — and on a device that sets no limit of its own,
                that session outlives the client: kvmd has no way to end one
                session, only every session a user has. Give this a value if
                the client is short-lived, so an abandoned session lapses.
            verify_ssl: What to trust; see
                [`VerifyTypes`][aiopikvm.VerifyTypes]. Off by default
                because PiKVM ships a self-signed certificate. Pass the path
                of a CA bundle for a device re-issued one from a private CA,
                or a ready-made `ssl.SSLContext` for anything else.
            cert: Client certificate to present: a combined PEM path, or
                ``(cert, key)``, or ``(cert, key, password)``. Cannot be
                combined with an `ssl.SSLContext` — load it into that
                context instead.
            proxy: Proxy URL to reach the device through. ``None`` leaves it
                to the environment, unless *trust_env* says otherwise.
            trust_env: Read proxy settings and the certificate bundle from
                the environment. ``False`` ignores ``HTTPS_PROXY`` and the
                rest, for a client that must reach the device directly.
            timeout: Default per-request timeout in seconds.
            follow_redirects: Follow HTTP redirects instead of raising
                [`RedirectError`][aiopikvm.RedirectError]. Off by default,
                because following one can hand the credential to the target,
                by rules that differ per transport and are not worth relying
                on. A socket follows one only within its own ``ws``/``wss``
                scheme, and there it repeats its handshake headers verbatim,
                so it carries whatever the mode sends; every other redirect
                is refused before anything is resent — the absolute
                ``https://`` a real server sends included. Over HTTP the
                ``X-KVMD-*`` pair travels anywhere, ``Authorization`` is
                dropped when the origin changes except on a plain
                ``http``→``https`` upgrade of the same host, and the session
                token travels as far as its cookie scope reaches — which
                covers the device's subdomains and its other ports, and for a
                token set by hand is every host. Treat all of it as reachable
                by whatever the redirect points at. The usual cause — an
                ``http://`` base URL that nginx redirects to ``https://`` —
                has in any case already put the credential on the wire in
                cleartext.
            http_client: Pre-built httpx client. When given, this client does
                not close it, and over HTTP the arguments above are ignored
                — except under ``auth="cookie"``, which still logs in through
                that client with the *user*, *passwd*, *totp* and
                *session_expire* from here. The sockets do not go through
                httpx at all: they read the URL and every transport argument
                from this constructor, and their credential from the mode —
                the password from here, or under ``auth="cookie"`` the
                session token out of that client's jar.
        """
        self._url = url.rstrip("/")
        self._user = user
        self._passwd = passwd
        self._totp = totp
        self._auth = auth
        self._session_expire = session_expire
        self._verify_ssl = verify_ssl
        self._cert = cert
        self._proxy = proxy
        self._trust_env = trust_env
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._external_client = http_client is not None
        self._client: httpx.AsyncClient | None = http_client
        self._entered = False
        self._closed = False
        # One login at a time. Without it every request in flight when a
        # session expires opens its own, and all but the last are orphaned
        # on the device until they time out.
        self._login_lock = asyncio.Lock()

    @property
    def _password(self) -> str:
        """Password with the TOTP code appended, read afresh each time.

        Returns:
            What kvmd is asked to check. A callable *totp* is called here, so
            the code is the one current when the request goes out rather than
            the one that was current when the client was built.
        """
        code = self._totp_code()
        return self._passwd if code is None else f"{self._passwd}{code}"

    def _totp_code(self) -> str | None:
        """Return the TOTP code to use right now.

        Returns:
            The code, or ``None`` when the client was built without one. A
            callable is called here, once per use, so nothing caches a code
            past the window it belongs to.
        """
        if self._totp is None:
            return None
        return self._totp() if callable(self._totp) else self._totp

    def _credential_headers(self) -> dict[str, str]:
        """Build the credential headers for this client's auth mode.

        Returns:
            The headers to send on this request. Empty for ``"cookie"``,
            which carries its credential in the jar instead.

        Raises:
            ConfigurationError: The credential is not ASCII, which is all an
                HTTP header can carry. The user name and password are checked
                when the client opens; the TOTP code cannot be, because a
                callable produces a new one per request — so this is where a
                non-ASCII code is caught. ``"basic"`` refuses it here too,
                rather than encoding it as UTF-8 and sending a credential
                kvmd reads as something else.
        """
        if self._auth == "cookie":
            return {}
        password = self._password
        try:
            raw = f"{self._user}:{password}".encode("ascii")
        except UnicodeEncodeError as exc:
            raise ConfigurationError(
                "PiKVM credentials travel in HTTP headers and must be ASCII, "
                f"and the TOTP code makes this one something else: {exc}"
            ) from exc
        if self._auth == "headers":
            return {"X-KVMD-User": self._user, "X-KVMD-Passwd": password}
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}

    def _outgoing_headers(self, headers: dict[str, str] | None) -> dict[str, str]:
        """Merge the credential headers into a call's own.

        Built per request rather than kept on the HTTP client, so that a
        rotating TOTP code is the one current when the request goes out.

        An external *http_client* is left to carry its own credentials, as
        it does for everything else this constructor takes.

        Args:
            headers: Headers the caller passed, if any.

        Returns:
            What to send. The caller's own win, so an explicit header can
            still override the client's credential for one request.

        Raises:
            ConfigurationError: If the credentials are not ASCII, which is
                all an HTTP header can carry.
        """
        if self._external_client:
            return dict(headers or {})
        merged = self._credential_headers()
        merged.update(headers or {})
        return merged

    # --- HTTP ----------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the underlying *httpx.AsyncClient*.

        Returns:
            The HTTP client every request goes through.

        Raises:
            PiKVMError: If this client has been closed, or the async context
                has not been entered yet.
        """
        if self._client is None:
            if self._closed:
                raise PiKVMError(
                    "This PiKVM client has been closed and cannot be used "
                    "again. Build a new one — a closed client cannot be "
                    "reopened, the same as httpx.AsyncClient."
                )
            raise PiKVMError(
                "Cannot access resources before entering async context. "
                "Use 'async with PiKVM(...) as kvm:' first."
            )
        return self._client

    @property
    def base_url(self) -> httpx.URL:
        """Base URL every request is sent relative to.

        Returns:
            The underlying client's base URL. With an external *http_client*
            this is whatever that client was configured with, not the *url*
            passed to this constructor.

        Raises:
            PiKVMError: If this client has been closed, or the async context
                has not been entered yet.
        """
        return self._ensure_client().base_url

    @property
    def cookies(self) -> httpx.Cookies:
        """Cookies the underlying HTTP client carries.

        [`AuthResource.login()`][aiopikvm.resources.auth.AuthResource.login]
        leaves kvmd's ``auth_token`` here, and every later request sends it
        back.

        Whether the token is the credential depends on the *auth* mode
        ([`AuthMode`][aiopikvm.AuthMode]). kvmd reads the ``X-KVMD-*``
        headers, then this cookie, then HTTP Basic, and the first source
        *present* decides the request — a token it does not know is refused
        outright rather than retried against what comes after it. So which
        mode this client is in settles what the jar is for.

        Under ``auth="headers"`` the pair goes out with every request and is
        read first, so a token here decides nothing. It is then only ever
        what authenticates an `httpx.AsyncClient` passed in as *http_client*
        without a credential of its own:

            async with httpx.AsyncClient(base_url=url, verify=False) as http:
                http.cookies.set("auth_token", saved_token)
                async with PiKVM(url, http_client=http) as kvm:
                    ...

        Under ``auth="basic"`` there is no ``X-KVMD-User``, so kvmd reaches
        this cookie *before* the Basic credential: a token left here by
        [`AuthResource.login()`][aiopikvm.resources.auth.AuthResource.login]
        authenticates every later request instead, and once it expires those
        requests fail although the password is good — this mode opens no
        session of its own to replace it. Drop the cookie to go back to the
        password.

        Under ``auth="cookie"`` the token is the credential: this client
        sends no ``X-KVMD-User`` at all, and what is in this jar is what
        every request and every socket handshake carries. The first request
        logs in on its own; [`ws()`][aiopikvm.PiKVM.ws] is not a coroutine
        and cannot, so opening a socket before anything else has authenticated
        raises rather than dialling with nothing.

        The other two modes hand [`ws()`][aiopikvm.PiKVM.ws] the *user* and
        *passwd* this client was built with, which are the defaults when an
        *http_client* carries the credentials instead.

        Returns:
            The live cookie jar — mutating it affects subsequent requests.
            Set a cookie through `httpx.Cookies.set()`; two entries of
            the same name under different domains make httpx's own lookup
            raise, which is why aiopikvm clears before it sets.

        Raises:
            PiKVMError: If this client has been closed, or the async context
                has not been entered yet.
        """
        return self._ensure_client().cookies

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        content: bytes | httpx.AsyncByteStream | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        """Send an HTTP request and return the raw response.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: URL path relative to the PiKVM base URL.
            params: Query parameters.
            json: JSON body.
            data: Form fields, sent as ``application/x-www-form-urlencoded``.
                kvmd reads ``/auth/login`` with aiohttp's form parser, which
                sees nothing in a JSON body. httpx picks one body per
                request, preferring ``content`` over ``data`` over ``json``,
                so pass exactly one of the three.
            content: Raw body bytes or async byte stream.
            headers: Extra HTTP headers.
            timeout: Override the client-level timeout for this request.

        Returns:
            The *httpx.Response* object.

        Raises:
            ConfigurationError: The base URL has no usable scheme, or the
                credential is not ASCII — which for a TOTP code produced by a
                callable is only known here.
            ConnectError: Connection to PiKVM failed or broke mid-request.
            ConnectionTimeoutError: Request timed out.
            AuthError: Authentication failed (401/403).
            BusyError: PiKVM is busy with another operation (409).
            UnavailableError: The subsystem is disabled or offline (503).
            RedirectError: PiKVM answered with a redirect (3xx) and the
                client was not created with ``follow_redirects=True`` — or it
                was, and the redirects formed a loop.
            ResponseError: The body did not survive its ``Content-Encoding``.
            APIError: Server returned any other error status (>= 400).
        """
        if self._needs_session(path):
            await self._ensure_session()
            carried = self._session_token()
            try:
                return await self._send(
                    method, path, params, json, data, content, headers, timeout
                )
            except AuthError:
                # The token was refused: expired, or logged out from
                # somewhere else. Open a session and try the call once more.
                # Anything wrong with the password itself fails again below,
                # this time for good.
                await self._ensure_session(refused=carried)
                if content is not None and not isinstance(content, bytes):
                    # Anything but bytes is a stream the first attempt has
                    # consumed. Sending it again delivers nothing and fails
                    # for a reason that has nothing to do with the refusal, so
                    # hand the refusal back — the session is open now, and the
                    # caller's own retry starts from a fresh body.
                    raise
        return await self._send(
            method, path, params, json, data, content, headers, timeout
        )

    def _needs_session(self, path: str) -> bool:
        """Whether this call has to carry a session token.

        Args:
            path: URL path the request is about to go to.

        Returns:
            ``True`` for a request that authenticates by cookie and is neither
            of the two endpoints that carry their own. The login needs no
            credential, and routing it through here would not terminate; the
            logout is aimed at one particular token, which it has already put
            in the jar, and a retry under a session opened here would drop
            that session instead of the one asked for.
        """
        return self._auth == "cookie" and not path.rstrip("/").endswith(
            ("/auth/login", "/auth/logout")
        )

    async def _ensure_session(self, *, refused: str | None = None) -> None:
        """Make sure the cookie jar holds a session token.

        Args:
            refused: The token kvmd has just refused, when this call is a
                refresh rather than a first login. It is compared with the jar
                under the lock: a token that is no longer the one in the jar
                has already been replaced by another task, and the replacement
                has not been tried yet, so this call has nothing to do.

        Raises:
            AuthError: The credentials were refused.
        """
        async with self._login_lock:
            stored = self._session_token()
            if refused is None:
                if stored:
                    return
            elif stored != refused:
                # Another task logged in while this one waited for the lock;
                # that token has not been tried yet.
                return
            else:
                # Drop the refused token before asking for a new one, so the
                # login itself does not carry it.
                self._ensure_client().cookies.delete(_COOKIE)
            await self.auth.login(
                self._user,
                self._passwd,
                self._totp_code(),
                expire=self._session_expire,
            )

    def _session_token(self) -> str:
        """Return the session token in the jar, if any.

        Walks the jar rather than calling ``httpx.Cookies.get``, which raises
        ``CookieConflict`` — outside the
        [`PiKVMError`][aiopikvm.PiKVMError] hierarchy — when two cookies
        share a name under different domains or paths. A valueless entry is
        passed over, so one filed after the real cookie cannot hide it; see
        `aiopikvm.resources.auth._token_in`, which reads a response's jar by
        the same two rules.

        Returns:
            The token, or ``""`` when there is none.
        """
        token = ""
        for cookie in self._ensure_client().cookies.jar:
            if cookie.name == _COOKIE and cookie.value:
                token = cookie.value
        return token

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        data: dict[str, str] | None,
        content: bytes | httpx.AsyncByteStream | None,
        headers: dict[str, str] | None,
        timeout: float | httpx.Timeout | None,
    ) -> httpx.Response:
        """Send one request and translate httpx's failures into this
        package's.

        Args:
            method: HTTP method.
            path: URL path relative to the base URL.
            params: Query parameters.
            json: JSON body.
            data: Form fields.
            content: Raw body.
            headers: Extra headers.
            timeout: Per-request timeout.

        Returns:
            The response, once its status has been checked.

        Raises:
            ConfigurationError: The base URL has no usable scheme, or the
                credential is not ASCII.
            ConnectError: The connection failed or broke mid-request.
            ConnectionTimeoutError: The request timed out.
            RedirectError: kvmd answered with a redirect, or they looped.
            ResponseError: The body did not survive its ``Content-Encoding``.
            APIError: Any other error status, and its subclasses.
        """
        client = self._ensure_client()
        with _httpx_errors_translated():
            response = await client.request(
                method,
                path,
                params=params,
                json=json,
                data=data,
                content=content,
                headers=self._outgoing_headers(headers),
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )

        self._raise_for_status(response)
        return response

    @classmethod
    def _raise_for_status(cls, response: httpx.Response) -> None:
        """Raise the exception matching an error status code.

        Args:
            response: The HTTP response to check.

        Raises:
            RedirectError: If the status code is a 3xx redirect.
            AuthError: If the status code is 401 or 403.
            BusyError: If the status code is 409.
            UnavailableError: If the status code is 503.
            APIError: If the status code is any other value >= 400.
        """
        status = response.status_code
        if status < 300:
            return

        if status < 400:
            # A redirect is reported from its Location alone; inside stream()
            # the body has not been read, and none of it would say anything a
            # caller could act on anyway.
            raise _status_error(status, location=response.headers.get("location", ""))

        error, error_msg = cls._error_fields(response)
        raise _status_error(
            status,
            error=error,
            error_msg=error_msg,
            detail=cls._body_excerpt(response),
        )

    @staticmethod
    def _error_fields(response: httpx.Response) -> tuple[str, str]:
        """Extract kvmd's error block from a response body.

        Args:
            response: The HTTP response to read.

        Returns:
            The ``(error, error_msg)`` pair, each empty when the body is not
            a kvmd error envelope or has not been read yet.
        """
        try:
            return _error_fields(response.json())
        except (ValueError, TypeError, httpx.ResponseNotRead):
            return ("", "")

    @staticmethod
    def _body_excerpt(response: httpx.Response, limit: int = 200) -> str:
        """Return the start of a response body, or ``""`` if it is unread."""
        try:
            return response.text[:limit]
        except httpx.ResponseNotRead:  # pragma: no cover - defensive
            return ""

    def _stream_timeout(self) -> httpx.Timeout:
        """Build the default timeout for a call that reads until kvmd stops.

        Returns:
            The underlying client's own timeout with the read one lifted: a
            stream has no end to wait for, so a gap between records is not a
            failure. Connect, write and pool keep their configured values —
            with an external *http_client* that means the ones it was built
            with, since ``timeout`` is among the constructor arguments this
            client documents as ignored when a client is passed in.

        Raises:
            PiKVMError: If this client has been closed, or the async context
                has not been entered yet.
        """
        base = self._ensure_client().timeout
        # The three are spelled out because httpx.Timeout() asserts when a
        # per-field override is passed beside a Timeout instance. Reading the
        # fields off it and handing them back is the same thing without it.
        return httpx.Timeout(
            connect=base.connect, read=None, write=base.write, pool=base.pool
        )

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncIterator[httpx.Response]:
        """Open a streaming HTTP connection.

        Args:
            method: HTTP method.
            path: URL path.
            params: Query parameters.
            headers: Extra HTTP headers.
            timeout: Override request timeout.

        Under ``auth="cookie"`` this opens a session first, and reopens one
        if the token is refused — the same preamble
        [`request()`][aiopikvm.PiKVM.request] runs. Nothing has been yielded
        when the refusal arrives, so the connection is simply made again.

        Yields:
            The *httpx.Response* with an unconsumed body.

        Raises:
            ConfigurationError: The base URL has no usable scheme, or the
                credential is not ASCII.
            ConnectError: Connection to PiKVM failed or broke mid-request.
            ConnectionTimeoutError: Request timed out.
            AuthError: Authentication failed (401/403).
            BusyError: PiKVM is busy with another operation (409).
            UnavailableError: The subsystem is disabled or offline (503).
            RedirectError: PiKVM answered with a redirect (3xx) and the
                client was not created with ``follow_redirects=True`` — or it
                was, and the redirects formed a loop.
            ResponseError: The body did not survive its ``Content-Encoding``.
                This one can arrive while the caller is reading: the
                translation covers the whole block, including the ``yield``.
            APIError: Server returned any other error status (>= 400).
        """
        with _httpx_errors_translated():
            if self._needs_session(path):
                await self._ensure_session()
                carried = self._session_token()
                try:
                    stack, response = await self._open_stream(
                        method, path, params, headers, timeout
                    )
                except AuthError:
                    # The token was refused: expired, or logged out from
                    # somewhere else. Nothing has reached the caller yet, so
                    # open a session and connect once more. Anything wrong
                    # with the password itself fails again, this time for
                    # good.
                    await self._ensure_session(refused=carried)
                    stack, response = await self._open_stream(
                        method, path, params, headers, timeout
                    )
            else:
                stack, response = await self._open_stream(
                    method, path, params, headers, timeout
                )
            async with stack:
                yield response

    async def _open_stream(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: float | httpx.Timeout | None,
    ) -> tuple[AsyncExitStack, httpx.Response]:
        """Connect once, and hand the open connection to its caller.

        The response comes back with its body unread and its connection
        live, so whoever asked for it decides when it ends: closing the
        returned stack closes the response. A connection that fails the
        status check is closed here instead — there is nothing left to read
        from it, and an attempt that is about to be made again must not hold
        a socket open meanwhile.

        Args:
            method: HTTP method.
            path: URL path relative to the PiKVM base URL.
            params: Query parameters.
            headers: Extra HTTP headers.
            timeout: Override the client-level timeout for this request.

        Returns:
            The stack that owns the open connection, and the response.

        Raises:
            AuthError: Authentication failed (401/403).
            BusyError: PiKVM is busy with another operation (409).
            UnavailableError: The subsystem is disabled or offline (503).
            RedirectError: PiKVM answered with a redirect (3xx) and the
                client was not created with ``follow_redirects=True``.
            APIError: Server returned any other error status (>= 400).
        """
        stack = AsyncExitStack()
        try:
            response = await stack.enter_async_context(
                self._ensure_client().stream(
                    method,
                    path,
                    params=params,
                    headers=self._outgoing_headers(headers),
                    timeout=(
                        timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
                    ),
                )
            )
            if response.status_code >= 400:
                # The body is still unread here, and kvmd's error block is
                # what makes the failure readable; reading it also keeps
                # response.text from raising httpx.ResponseNotRead.
                await response.aread()
            self._raise_for_status(response)
        except BaseException:
            await stack.aclose()
            raise
        return stack, response

    # --- Resources (lazy) ----------------------------------------------

    @cached_property
    def auth(self) -> AuthResource:
        """Authentication resource."""
        from aiopikvm.resources.auth import AuthResource

        self._ensure_client()
        return AuthResource(self)

    @cached_property
    def atx(self) -> ATXResource:
        """ATX power control resource."""
        from aiopikvm.resources.atx import ATXResource

        self._ensure_client()
        return ATXResource(self)

    @cached_property
    def hid(self) -> HIDResource:
        """HID keyboard and mouse resource."""
        from aiopikvm.resources.hid import HIDResource

        self._ensure_client()
        return HIDResource(self)

    @cached_property
    def msd(self) -> MSDResource:
        """Mass Storage Device resource."""
        from aiopikvm.resources.msd import MSDResource

        self._ensure_client()
        return MSDResource(self)

    @cached_property
    def gpio(self) -> GPIOResource:
        """GPIO channels resource."""
        from aiopikvm.resources.gpio import GPIOResource

        self._ensure_client()
        return GPIOResource(self)

    @cached_property
    def streamer(self) -> StreamerResource:
        """Streamer snapshots and OCR resource."""
        from aiopikvm.resources.streamer import StreamerResource

        self._ensure_client()
        return StreamerResource(self)

    @cached_property
    def media(self) -> MediaResource:
        """Live video from the kvmd-media daemon."""
        from aiopikvm.resources.media import MediaResource

        self._ensure_client()
        return MediaResource(self)

    @cached_property
    def switch(self) -> SwitchResource:
        """Multi-port KVM switch resource."""
        from aiopikvm.resources.switch import SwitchResource

        self._ensure_client()
        return SwitchResource(self)

    @cached_property
    def redfish(self) -> RedfishResource:
        """Redfish DMTF BMC resource."""
        from aiopikvm.resources.redfish import RedfishResource

        self._ensure_client()
        return RedfishResource(self)

    @cached_property
    def prometheus(self) -> PrometheusResource:
        """Prometheus metrics resource."""
        from aiopikvm.resources.prometheus import PrometheusResource

        self._ensure_client()
        return PrometheusResource(self)

    @cached_property
    def system(self) -> SystemResource:
        """System information and logs resource."""
        from aiopikvm.resources.system import SystemResource

        self._ensure_client()
        return SystemResource(self)

    # --- Context manager -----------------------------------------------

    async def __aenter__(self) -> Self:
        """Open the client.

        Returns:
            This client, ready to use.

        Raises:
            ConfigurationError: If this client is already open or has been
                closed, or if the URL or credentials cannot be used to build
                an HTTP client.
        """
        if self._closed:
            raise ConfigurationError(
                "Cannot reopen a PiKVM client once it has been closed. Build a new one."
            )
        if self._entered:
            raise ConfigurationError(
                "Cannot enter a PiKVM client more than once: the inner block "
                "would close the connection the outer one is still using."
            )
        if self._client is None:
            # The credentials go on each request rather than on the client,
            # so that a rotating TOTP code is current when it is sent. They
            # are still checked here: an unusable password should be a
            # failure to open the client, not a surprise on the first call.
            try:
                f"{self._user}{self._passwd}".encode("ascii")
            except UnicodeEncodeError as exc:
                raise ConfigurationError(
                    f"PiKVM credentials travel in HTTP headers and must be ASCII: {exc}"
                ) from exc
            try:
                self._client = httpx.AsyncClient(
                    base_url=self._url,
                    # One context for both halves of the client, and the
                    # only spelling httpx 0.28 does not deprecate: `cert=`
                    # and `verify=<path>` both tell you to build this.
                    verify=build_ssl_context(self._verify_ssl, self._cert),
                    proxy=self._proxy,
                    trust_env=self._trust_env,
                    timeout=self._timeout,
                    follow_redirects=self._follow_redirects,
                )
            except (httpx.InvalidURL, ValueError) as exc:
                # httpx.InvalidURL is not a ValueError, and a proxy URL it
                # cannot read is a plain one. With trust_env left on, that
                # proxy comes from HTTPS_PROXY — so a malformed value in the
                # environment fails the open of a client the program built
                # correctly, and it has to say so as a PiKVMError.
                raise ConfigurationError(
                    f"Cannot build an HTTP client for {self._url!r}: {exc}. "
                    "The proxy URL counts too, including one read from the "
                    "environment; pass trust_env=False to ignore those."
                ) from exc
        self._entered = True
        return self

    async def aclose(self) -> None:
        """Close the client and release resources.

        An HTTP client built here is closed; one handed in as *http_client*
        is left alone, since the caller owns it. Either way this client lets
        go of it and will not serve another request: the alternative is an
        object that keeps working after the block that owned it ended, which
        is only ever a bug waiting to be found somewhere else.

        Calling this more than once does nothing the second time.
        """
        for name in _RESOURCE_NAMES:
            self.__dict__.pop(name, None)

        if not self._external_client and self._client is not None:
            await self._client.aclose()

        self._client = None
        self._entered = False
        self._closed = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # --- WebSocket -----------------------------------------------------

    def ws(
        self,
        *,
        stream: bool = True,
        binary: bool = False,
        open_timeout: float | None = None,
        close_timeout: float | None = None,
        max_size: int | None = _WS_MAX_SIZE,
        max_queue: int = _WS_MAX_QUEUE,
        ping_interval: float | None = _WS_PING_INTERVAL,
        ping_timeout: float | None = _WS_PING_TIMEOUT,
    ) -> PiKVMWebSocket:
        """Create a WebSocket connection.

        The socket carries whichever credential this client's *auth* mode
        says. Under ``"headers"`` and ``"basic"`` those are the *user* and
        *passwd* it was built with; under ``"cookie"`` it is the session
        token from [`cookies`][aiopikvm.PiKVM.cookies], read when the socket
        is opened rather than here — so something must have logged in by
        then, though not necessarily before this call. Neither this method
        nor the socket logs in: it is a request that opens a session, or
        [`AuthResource.login()`][aiopikvm.resources.auth.AuthResource.login].

        Args:
            stream: Count this client as a video viewer, which is also kvmd's
                own default. kvmd runs the streamer while at least one
                connected session asked for it, so a socket opened with
                ``False`` lets the video pipeline stop — and
                [`StreamerResource.snapshot()`][aiopikvm.resources.streamer.StreamerResource.snapshot]
                then answers HTTP 503 unless something else is watching. Pass
                ``False`` only for a client that reads events and never looks
                at the picture.
            binary: Send HID input over kvmd's binary channel instead of as
                JSON events, the way kvmd's own web UI does. Both reach the
                same handlers; see
                [`PiKVMWebSocket`][aiopikvm.PiKVMWebSocket].
            open_timeout: Timeout for opening the connection (defaults to
                the client *timeout*).
            close_timeout: Timeout for closing the connection (defaults to
                the client *timeout*).
            max_size: Largest frame to accept, in bytes, or ``None`` for no
                limit. kvmd's events are small; the cap is *websockets*' own.
            max_queue: How many frames the transport may buffer before it
                pauses reading. The socket is drained continuously, so this
                is here for a caller who knows their case is unusual.
            ping_interval: Seconds between the protocol keepalive pings, or
                ``None`` to send none — which leaves a link that dies
                silently looking open. This is *websockets*' keepalive, not
                [`PiKVMWebSocket.ping()`][aiopikvm.PiKVMWebSocket.ping].
            ping_timeout: Seconds to wait for a keepalive pong before the
                connection is failed, or ``None`` to wait forever.

        Returns:
            A *PiKVMWebSocket* async context manager. It inherits this
            client's *verify_ssl* and *follow_redirects*. It does not go
            through httpx, so with an external *http_client* it still dials
            the URL passed to this constructor — but under ``auth="cookie"``
            its credential is the session token in that client's jar, not
            anything from here.

        Raises:
            ConfigurationError: If this client has been closed, or the URL it
                was built with has no usable scheme. Under ``auth="cookie"``
                a missing session token is reported too, but only once the
                socket is entered: the token is read at the handshake, so one
                minted in between is the one that goes out.
        """
        self._ws_usable()
        return PiKVMWebSocket(
            url=self._url,
            user=self._user,
            # The property, not its value: read when the handshake is made.
            passwd=lambda: self._password,
            auth=self._auth,
            # The token as it is when the handshake is made, not now.
            token=lambda: self._ws_token("ws()"),
            verify_ssl=self._verify_ssl,
            cert=self._cert,
            proxy=self._proxy,
            trust_env=self._trust_env,
            stream=stream,
            binary=binary,
            follow_redirects=self._follow_redirects,
            open_timeout=open_timeout if open_timeout is not None else self._timeout,
            close_timeout=close_timeout if close_timeout is not None else self._timeout,
            max_size=max_size,
            max_queue=max_queue,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )

    def media_ws(
        self,
        *,
        video: str | None = "h264",
        max_size: int | None = None,
        max_queue: int | None = None,
        ping_interval: float | None = _WS_PING_INTERVAL,
        ping_timeout: float | None = _WS_PING_TIMEOUT,
        open_timeout: float | None = None,
        close_timeout: float | None = None,
    ) -> MediaWebSocket:
        """Open a live video socket to the kvmd-media daemon.

        This is a different daemon from the one
        [`ws()`][aiopikvm.PiKVM.ws] talks to, and it does not count as a video
        viewer: kvmd runs the streamer while at least one *kvmd* session asks
        for video, and this socket is not one. Hold a
        [`ws()`][aiopikvm.PiKVM.ws] open alongside it, or the frames stop
        arriving with nothing to say why.

        The socket carries whichever credential this client's *auth* mode
        says, the same way [`ws()`][aiopikvm.PiKVM.ws] does.

        Args:
            video: The format to stream. Naming one opens the pure socket,
                which starts sending during the handshake and sends nothing
                but raw frames; ``None`` opens the regular one, which waits
                for [`MediaWebSocket.start()`][aiopikvm.MediaWebSocket.start]
                and flags its keyframes. A format the daemon does not serve is
                refused with HTTP 400 during the handshake.
            max_size: Largest message to accept, in bytes. ``None``, the
                default, accepts any — a message here is one video frame, and
                a limit does not truncate an oversized one, it closes the
                connection.
            max_queue: How many frames to buffer before *websockets* stops
                reading the socket. ``None`` takes this client's default,
                which is larger than the *websockets* one: once the buffer is
                full *websockets* pauses the transport, and because it parses
                everything — its own keepalive pongs included — only while
                reading, a consumer that stalls for longer than *ping_timeout*
                has its healthy connection closed underneath it. Raising this
                buys slack; ``ping_interval=None`` removes the trap and the
                dead-link detection with it.
            ping_interval: Seconds between *websockets*' own keepalive pings,
                ``None`` to send none.
            ping_timeout: Seconds to wait for a keepalive pong before
                declaring the link dead, ``None`` to wait forever.
            open_timeout: Timeout for opening the connection (defaults to the
                client *timeout*).
            close_timeout: Timeout for closing the connection (defaults to the
                client *timeout*).

        Returns:
            A *MediaWebSocket* async context manager. It inherits this
            client's *verify_ssl*, proxy configuration and *follow_redirects*.

        Raises:
            ConfigurationError: If this client has been closed, or the URL it
                was built with has no usable scheme. Under ``auth="cookie"``
                a missing session token is reported too, but only once the
                socket is entered: the token is read at the handshake, so one
                minted in between is the one that goes out.
        """
        self._ws_usable()
        return MediaWebSocket(
            url=self._url,
            user=self._user,
            # The property, not its value: read when the handshake is made.
            passwd=lambda: self._password,
            auth=self._auth,
            # The token as it is when the handshake is made, not now.
            token=lambda: self._ws_token("media_ws()"),
            verify_ssl=self._verify_ssl,
            cert=self._cert,
            proxy=self._proxy,
            trust_env=self._trust_env,
            video=video,
            follow_redirects=self._follow_redirects,
            open_timeout=open_timeout if open_timeout is not None else self._timeout,
            close_timeout=close_timeout if close_timeout is not None else self._timeout,
            max_size=max_size,
            max_queue=max_queue,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )

    def webrtc(
        self,
        *,
        audio: bool = False,
        orientation: int = 0,
        ice_servers: Sequence[str] | None = None,
        frame_buffer: int = _FRAME_BUFFER,
        keepalive_interval: float = _KEEPALIVE_INTERVAL,
        open_timeout: float | None = None,
        close_timeout: float | None = None,
        negotiate_timeout: float = _NEGOTIATE_TIMEOUT,
        ping_interval: float | None = _WS_PING_INTERVAL,
        ping_timeout: float | None = _WS_PING_TIMEOUT,
    ) -> WebRTCSession:
        """Open a WebRTC session against the device's Janus gateway.

        This is the lowest-latency of the three video paths, and the one
        kvmd's own web UI takes. It is also the only one that needs an extra:
        ``pip install 'aiopikvm[webrtc]'``, for aiortc and the FFmpeg it
        bundles. The frames it hands over are decoded, where
        [`media_ws()`][aiopikvm.PiKVM.media_ws] hands over the encoded stream
        and [`StreamerResource`][aiopikvm.resources.streamer.StreamerResource]
        hands over MJPEG.

        Like [`media_ws()`][aiopikvm.PiKVM.media_ws], this needs a
        [`ws()`][aiopikvm.PiKVM.ws] held open beside it. kvmd runs ustreamer
        only while a session has asked to be counted as a viewer, and the
        Janus plugin reads its frames out of ustreamer, so without one the
        negotiation succeeds in every visible way — Janus even reports the
        peer connection up — and not a single frame ever arrives.

        The signalling socket carries whichever credential this client's
        *auth* mode says, the same way [`ws()`][aiopikvm.PiKVM.ws] does. The
        media does not: it is UDP between this process and the device, and it
        is secured by DTLS-SRTP rather than by TLS.

        Args:
            audio: Ask for the host's audio alongside the video. The device
                needs a capture device for it, which
                [`WebRTCSession.features`][aiopikvm.WebRTCSession.features]
                reports.
            orientation: Rotate the video, ``0``, ``90``, ``180`` or ``270``.
            ice_servers: STUN or TURN URLs to gather candidates through.
                ``None``, the default, uses none: host candidates reach a
                device on the same network, and a STUN server is a third
                party this client will not contact uninvited.
            frame_buffer: How many decoded frames to hold per track before the
                oldest is dropped. Live video wants this small.
            keepalive_interval: Seconds between Janus session keepalives.
                Janus drops a session silent for sixty.
            open_timeout: Timeout for opening the connection and for each
                individual Janus message (defaults to the client *timeout*).
            close_timeout: Timeout for closing the connection (defaults to the
                client *timeout*).
            negotiate_timeout: Seconds to allow the whole negotiation, from
                the session being created to the peer connection coming up.
            ping_interval: Seconds between *websockets*' own keepalive pings
                on the signalling socket, ``None`` to send none.
            ping_timeout: Seconds to wait for a keepalive pong before
                declaring the signalling link dead, ``None`` to wait forever.

        Returns:
            A *WebRTCSession* async context manager. It inherits this client's
            *verify_ssl*, proxy configuration and *follow_redirects*.

        Raises:
            ConfigurationError: If this client has been closed, or the URL it
                was built with has no usable scheme. The missing ``webrtc``
                extra is reported here too, and so is a missing session token
                under ``auth="cookie"`` — but both only once the session is
                entered. The token is read at the handshake, so one minted in
                between is the one that goes out.
        """
        self._ws_usable()
        return WebRTCSession(
            url=self._url,
            user=self._user,
            # The property, not its value: read when the handshake is made.
            passwd=lambda: self._password,
            auth=self._auth,
            # The token as it is when the handshake is made, not now.
            token=lambda: self._ws_token("webrtc()"),
            verify_ssl=self._verify_ssl,
            cert=self._cert,
            proxy=self._proxy,
            trust_env=self._trust_env,
            audio=audio,
            orientation=orientation,
            ice_servers=ice_servers,
            frame_buffer=frame_buffer,
            keepalive_interval=keepalive_interval,
            follow_redirects=self._follow_redirects,
            open_timeout=open_timeout if open_timeout is not None else self._timeout,
            close_timeout=close_timeout if close_timeout is not None else self._timeout,
            negotiate_timeout=negotiate_timeout,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )

    def _ws_usable(self) -> None:
        """Refuse to build a socket on a client that can no longer open one.

        Raises:
            ConfigurationError: This client has been closed.
        """
        if self._closed:
            raise ConfigurationError(
                "This PiKVM client has been closed; it cannot open a new "
                "WebSocket. Build a new client."
            )

    def _ws_token(self, what: str) -> str:
        """Find the session token a WebSocket handshake needs, if it needs one.

        Read when the handshake is made rather than when the socket was built,
        the way the password is. Under ``auth="cookie"`` the token is not the
        client's to keep: a request mints one, kvmd refusing it mints another,
        and an explicit login replaces both. Reading it here is what lets a
        socket built before any of that carry the token that is current when
        it opens — and a socket reopened later carry the one current then,
        rather than the one it was built with.

        Args:
            what: Name of the method that built the socket, for the error
                message.

        Returns:
            The token under ``auth="cookie"``, otherwise an empty string.

        Raises:
            ConfigurationError: This client has been closed, or it is using
                cookie auth and there is no session token to read — either
                because it has not been entered, so there is no cookie jar at
                all, or because nothing has logged in yet.
        """
        self._ws_usable()
        if self._auth != "cookie":
            return ""
        if self._client is None:
            raise ConfigurationError(
                f"auth='cookie' reads its session token from this client's "
                f"cookie jar, and the client has not been entered, so there "
                f"is no jar to read. Open it with 'async with PiKVM(...) as "
                f"kvm:' and open the {what} socket inside that block."
            )
        token = self._session_token()
        if not token:
            raise ConfigurationError(
                f"auth='cookie' has no session token to open a WebSocket "
                f"with. {what} cannot log in — it is not a coroutine — so "
                "call 'await kvm.auth.login(user, passwd)', or make any "
                "request first, and open the socket after that."
            )
        return token


_RESOURCE_NAMES = tuple(
    name for name, value in vars(PiKVM).items() if isinstance(value, cached_property)
)
"""Every lazily-built resource on [`PiKVM`][aiopikvm.PiKVM], in declaration order.

Read off the class rather than listed beside it. `aclose()` clears each of
these out of the instance `__dict__`, so a name missing from the list is a
resource that outlives the close: the getter is what raises `PiKVMError` on a
client that is gone, and one already in `__dict__` is handed back without it
ever running again — and a list nothing derives is one a new resource is
added without (#143).

Every `cached_property` on this class is a resource today, and the tests hold
that: one of them asserts each name here builds a `BaseResource`, so a cached
property of some other kind would fail rather than be quietly cleared.
"""
