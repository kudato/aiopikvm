"""PiKVM async client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

import httpx

from aiopikvm._constants import (
    DEFAULT_FOLLOW_REDIRECTS,
    DEFAULT_TIMEOUT,
    DEFAULT_TRUST_ENV,
    DEFAULT_VERIFY_SSL,
)
from aiopikvm._exceptions import (
    ConfigurationError,
    ConnectError,
    ConnectionTimeoutError,
    PiKVMError,
    RedirectError,
    _error_fields,
    _status_error,
)
from aiopikvm._transport import (
    VerifySSL,
    httpx_environment_proxies,
    mask_proxy,
    proxy_environment_variables,
    resolve_proxy,
    resolve_verify_ssl,
)
from aiopikvm._ws import PiKVMWebSocket

if TYPE_CHECKING:
    from types import TracebackType

    from aiopikvm.resources.atx import ATXResource
    from aiopikvm.resources.auth import AuthResource
    from aiopikvm.resources.gpio import GPIOResource
    from aiopikvm.resources.hid import HIDResource
    from aiopikvm.resources.msd import MSDResource
    from aiopikvm.resources.prometheus import PrometheusResource
    from aiopikvm.resources.redfish import RedfishResource
    from aiopikvm.resources.streamer import StreamerResource
    from aiopikvm.resources.switch import SwitchResource
    from aiopikvm.resources.system import SystemResource

_RESOURCE_NAMES = (
    "auth",
    "atx",
    "hid",
    "msd",
    "gpio",
    "streamer",
    "switch",
    "redfish",
    "prometheus",
    "system",
)


def _leaves(group: BaseExceptionGroup[BaseException]) -> Iterator[BaseException]:
    """Every exception a group holds, in the order it holds them.

    anyio nests one task group inside another, so what a group says is what
    its leaves say; the groups on the way down carry nothing but a count.

    Args:
        group: The group to look inside, however deeply it nests.

    Yields:
        Each exception that is not itself a group.
    """
    pending: list[BaseException] = list(group.exceptions)
    while pending:
        exc = pending.pop(0)
        if isinstance(exc, BaseExceptionGroup):
            pending.extend(exc.exceptions)
        else:
            yield exc


def _grouped_failure(
    group: ExceptionGroup[Exception],
) -> type[ConnectionTimeoutError] | type[ConnectError] | None:
    """Which failure a group amounts to, when it is one this client can name.

    httpx maps what it knows into its own exceptions one at a time, and a
    failure that comes out of a task group is not one of them: the group
    itself is a plain ``ExceptionGroup``, so the clauses above it never fire.
    That happens on both sides of the call — inside httpcore, where anyio
    connects, and inside the caller's own code, which reaches here through an
    async request body, an event hook, or a transport of their making. Only
    the leaves say which it was, so they decide: a group made of nothing but
    connection failures is one, and a group holding anything else is the
    caller's to handle with ``except*``, whole.

    An :class:`OverflowError` counts as a connection failure because it is
    the one this client was written for: a proxy port outside 0-65535, which
    ``connect()`` raises about from inside anyio's task group. A leaf that is
    an :class:`OSError` does not — anyio gathers those into a single
    ``OSError`` of its own, so a grouped one is far likelier to be a caller
    writing what they read to a full disk than a socket failing.

    Args:
        group: The group that came out of httpx.

    Returns:
        The exception class to report it as, or ``None`` when it is not this
        client's to name.
    """
    leaves = list(_leaves(group))
    if all(isinstance(exc, httpx.TimeoutException) for exc in leaves):
        return ConnectionTimeoutError
    if all(isinstance(exc, httpx.TransportError | OverflowError) for exc in leaves):
        return ConnectError
    return None


class PiKVM:
    """Async client for PiKVM API.

    Usage::

        async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
            await kvm.atx.power_on()

    An external *httpx.AsyncClient* can be provided via *http_client*; in that
    case the caller is responsible for closing it.

    The lifecycle follows the one *httpx.AsyncClient* has, so that wrapping
    one does not change the rules: a client is used once and then closed.
    :meth:`aclose` — which ``async with`` calls on the way out — releases the
    resources and leaves the object closed for good, whether the underlying
    HTTP client was built here or handed in.

    Reopening and nesting both raise :class:`ConfigurationError`. Reopening
    used to build a second connection pool under the same object, rereading
    the credentials as they stood at that moment; nesting used to leave the
    inner block's exit closing the connection the outer one was still using.
    """

    def __init__(
        self,
        url: str,
        *,
        user: str = "admin",
        passwd: str = "",
        totp: str | None = None,
        verify_ssl: VerifySSL = DEFAULT_VERIFY_SSL,
        proxy: str | None = None,
        trust_env: bool = DEFAULT_TRUST_ENV,
        timeout: float = DEFAULT_TIMEOUT,
        follow_redirects: bool = DEFAULT_FOLLOW_REDIRECTS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client.

        Args:
            url: PiKVM base URL, including the scheme.
            user: kvmd user name.
            passwd: kvmd password.
            totp: Current TOTP code, appended to the password.
            verify_ssl: Verify the TLS certificate. Off by default because
                PiKVM ships a self-signed one. A device behind a private CA
                takes the path to that CA's bundle instead — a PEM file, or
                a directory of them — and anything else takes an
                :class:`ssl.SSLContext`, which is also where a client
                certificate goes::

                    context = ssl.create_default_context(cafile="ca.pem")
                    context.load_cert_chain("client.pem", "client.key")

            proxy: Proxy URL for every request, e.g.
                ``http://proxy.local:3128``. A ``socks5://`` proxy needs the
                ``socksio`` package httpx asks for and the ``python-socks``
                one *websockets* asks for; neither is a dependency of
                aiopikvm. ``None`` leaves the choice to *trust_env*.
            trust_env: Read the proxy settings from the environment —
                ``HTTPS_PROXY``, ``NO_PROXY`` and the rest. On by default,
                which is what httpx and *websockets* both do on their own;
                in httpx it also governs ``SSL_CERT_FILE`` and
                ``SSL_CERT_DIR``, which are read only when *verify_ssl* is
                ``True``.
            timeout: Default per-request timeout in seconds.
            follow_redirects: Follow HTTP redirects instead of raising
                :class:`RedirectError`. Off by default: a redirect resends
                the credential headers to whatever it points at, and the
                usual cause — an ``http://`` base URL that nginx redirects
                to ``https://`` — has already exposed the password in
                cleartext by then.
            http_client: Pre-built httpx client. When given, this client
                does not close it and the arguments above reach no request,
                since the caller configured those on the client they built.
                :meth:`ws` is the exception: it does not go through httpx at
                all, and goes on reading *url*, *user*, *passwd*, *totp*,
                *verify_ssl*, *proxy*, *trust_env*, *timeout* and
                *follow_redirects* from here. So with an external client
                these arguments configure the socket alone, and the two
                halves can end up set up differently.

        Raises:
            ConfigurationError: *verify_ssl* names a path this machine
                cannot load a CA bundle from, or *proxy* is one the two
                libraries would not use alike. A proxy the *environment*
                sets is left to httpx, whose rules for reading it are its
                own; what it raises about one is reported when the client is
                entered, if httpx refuses the URL outright, and otherwise as
                a :class:`ConnectError` when the request goes out.
        """
        self._url = url.rstrip("/")
        self._user = user
        self._passwd = passwd
        self._totp = totp
        self._verify_ssl = resolve_verify_ssl(verify_ssl)
        self._proxy = resolve_proxy(proxy)
        self._trust_env = trust_env
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._external_client = http_client is not None
        self._client: httpx.AsyncClient | None = http_client
        self._entered = False
        self._closed = False

    @property
    def _password(self) -> str:
        """Password with optional TOTP code appended."""
        return self._passwd if self._totp is None else f"{self._passwd}{self._totp}"

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

        :meth:`AuthResource.login` leaves kvmd's ``auth_token`` here, and
        every later request sends it back.

        Putting a token here is not enough to authenticate by session,
        though. kvmd tries the ``X-KVMD-*`` headers first and, once it sees a
        non-empty ``X-KVMD-User``, either accepts that pair or refuses the
        request outright — it never falls through to the cookie. Since this
        client always sends the header, the token is only ever the credential
        for an :class:`httpx.AsyncClient` passed in as *http_client* without
        those headers::

            async with httpx.AsyncClient(base_url=url, verify=False) as http:
                http.cookies.set("auth_token", saved_token)
                async with PiKVM(url, http_client=http) as kvm:
                    ...

        :meth:`ws` does not take part in this. The WebSocket authenticates
        with the *user* and *passwd* this client was built with, which are
        the defaults when an *http_client* carries the credentials instead.

        Returns:
            The live cookie jar — mutating it affects subsequent requests.
            Set a cookie through :meth:`httpx.Cookies.set`; two entries of
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

        What the caller's own code raises on the way — a streamed *content*
        body, an event hook or a transport on an external client — is not
        rewritten into one of these: it reaches them as it was raised, the
        structure of an ``ExceptionGroup`` included, so ``except*`` still
        picks it apart.

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
            ConfigurationError: The base URL has no usable scheme.
            ConnectError: Connection to PiKVM failed or broke mid-request.
            ConnectionTimeoutError: Request timed out.
            AuthError: Authentication failed (401/403).
            BusyError: PiKVM is busy with another operation (409).
            UnavailableError: The subsystem is disabled or offline (503).
            RedirectError: PiKVM answered with a redirect (3xx) and the
                client was not created with ``follow_redirects=True`` — or it
                was, and the redirects formed a loop.
            APIError: Server returned any other error status (>= 400).
        """
        client = self._ensure_client()
        try:
            response = await client.request(
                method,
                path,
                params=params,
                json=json,
                data=data,
                content=content,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException as exc:
            raise ConnectionTimeoutError(str(exc)) from exc
        except httpx.TooManyRedirects as exc:
            # Only reachable with follow_redirects=True. httpx derives it
            # from RequestError rather than TransportError, so the clause
            # below does not cover it and it would escape PiKVMError.
            raise RedirectError(
                f"{exc} Point the client at the URL the redirects lead to."
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
        except ExceptionGroup as exc:
            failed = _grouped_failure(exc)
            if failed is None:
                # Nothing in it is a connection failure, so it came out of the
                # caller's own code — the body iterator this request is
                # sending, an event hook, a transport of their making. Calling
                # that a failure to connect would bury what they were doing
                # and take ``except*`` away from them.
                raise
            raise failed(self._connection_failed(exc)) from exc

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

        An exception raised inside the ``async with`` block arrives here on
        its way out, and is left as it is unless it is a transport failure —
        one raised while the body was being read is the connection failing,
        whether or not the caller wrapped it in a task group of their own.

        Args:
            method: HTTP method.
            path: URL path.
            params: Query parameters.
            headers: Extra HTTP headers.
            timeout: Override request timeout.

        Yields:
            The *httpx.Response* with an unconsumed body.

        Raises:
            ConfigurationError: The base URL has no usable scheme.
            ConnectError: Connection to PiKVM failed or broke mid-request.
            ConnectionTimeoutError: Request timed out.
            AuthError: Authentication failed (401/403).
            BusyError: PiKVM is busy with another operation (409).
            UnavailableError: The subsystem is disabled or offline (503).
            RedirectError: PiKVM answered with a redirect (3xx) and the
                client was not created with ``follow_redirects=True`` — or it
                was, and the redirects formed a loop.
            APIError: Server returned any other error status (>= 400).
        """
        client = self._ensure_client()
        # An exception raised in the caller's own block arrives back here, at
        # the yield, and is caught by the clauses below. That is what maps a
        # transport failure met while the body is being read — and what makes
        # a group need looking inside, since one raised in their block reaches
        # the same clause as one raised while connecting.
        try:
            async with client.stream(
                method,
                path,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            ) as response:
                if response.status_code >= 400:
                    # The body is still unread here, and kvmd's error block is
                    # what makes the failure readable; reading it also keeps
                    # response.text from raising httpx.ResponseNotRead.
                    await response.aread()
                self._raise_for_status(response)
                yield response
        except httpx.TimeoutException as exc:
            raise ConnectionTimeoutError(str(exc)) from exc
        except httpx.TooManyRedirects as exc:
            # Only reachable with follow_redirects=True. httpx derives it
            # from RequestError rather than TransportError, so the clause
            # below does not cover it and it would escape PiKVMError.
            raise RedirectError(
                f"{exc} Point the client at the URL the redirects lead to."
            ) from exc
        except httpx.UnsupportedProtocol as exc:
            raise ConfigurationError(
                f"{exc} Pass the scheme in the PiKVM URL, e.g. https://pikvm.local."
            ) from exc
        except httpx.TransportError as exc:
            raise ConnectError(str(exc)) from exc
        except ExceptionGroup as exc:
            failed = _grouped_failure(exc)
            if failed is None:
                # Their block raised, and not about the connection. Reading
                # it as a failure to connect would bury what they were doing.
                raise
            raise failed(self._connection_failed(exc)) from exc

    def _connection_failed(self, group: ExceptionGroup[Exception]) -> str:
        """Spell out a connect failure httpcore had no name for.

        httpx maps what it knows into its own exceptions; what it does not
        map comes out of the task group anyio connects in, as an
        ``ExceptionGroup`` that prints how many exceptions it holds and none
        of what they say. A proxy port outside 0-65535 arrives this way, as
        an ``OverflowError`` from ``connect()``. A group can also reach here
        already carrying httpx's own exceptions, when the caller reads the
        body inside a task group of their own; either way the proxy in use is
        worth naming — by variable rather than by value, the environment's
        being shared with every other program on the machine.

        The proxy is only named when this client configured it. With an
        external *http_client* these settings reached the WebSocket alone,
        and the request that failed went through a client somebody else
        configured, whose proxy is not this object's to guess at.

        Args:
            group: The group that came out of httpx.

        Returns:
            What every exception in it said, and where a proxy could be
            standing in the way.
        """
        said = [f"{type(exc).__name__}: {exc}" for exc in _leaves(group)]
        if self._external_client:
            return "; ".join(said)
        if self._proxy is not None:
            said.append(
                f"the connection goes through the proxy {mask_proxy(self._proxy)!r}"
            )
        elif self._trust_env and (settings := httpx_environment_proxies()):
            if names := proxy_environment_variables(settings):
                said.append(
                    f"the environment sets {', '.join(names)}, which httpx may read"
                )
            else:
                # getproxies() answers with the system-wide settings on macOS
                # and Windows when the environment holds nothing, and httpx
                # reads it that way too. Naming a variable here would name one
                # nobody set.
                for_what = ", ".join(sorted(settings))
                said.append(
                    f"this machine is configured with a proxy for {for_what}, "
                    "which httpx may read"
                )
        return "; ".join(said)

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
                closed, or if the URL, the credentials, the proxy or the CA
                bundle the environment names cannot be used to build an HTTP
                client.
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
            try:
                self._client = httpx.AsyncClient(
                    base_url=self._url,
                    headers={
                        "X-KVMD-User": self._user,
                        "X-KVMD-Passwd": self._password,
                    },
                    verify=self._verify_ssl,
                    proxy=self._proxy,
                    trust_env=self._trust_env,
                    timeout=self._timeout,
                    follow_redirects=self._follow_redirects,
                )
            except UnicodeEncodeError as exc:
                raise ConfigurationError(
                    f"PiKVM credentials travel in HTTP headers and must be ASCII: {exc}"
                ) from exc
            except httpx.InvalidURL as exc:
                # A proxy carrying a port httpx reports this way was refused
                # in __init__, where the message could name it. What is left
                # is the base URL — unless the environment sets a proxy that
                # only httpx's parser is strict about, such as one holding a
                # tab, so the blame is shared only when there is one to
                # share it with.
                blamed = f"the PiKVM URL {self._url!r}"
                if (
                    self._proxy is None
                    and self._trust_env
                    and httpx_environment_proxies()
                ):
                    blamed += " or a proxy the environment sets"
                raise ConfigurationError(f"httpx refused {blamed}: {exc}") from exc
            except (ImportError, ValueError) as exc:
                # A proxy scheme httpx does not know arrives as a ValueError
                # and a socks:// one without the socksio package as an
                # ImportError, whichever of *proxy* and the environment the
                # URL came from. Both are outside PiKVMError.
                raise ConfigurationError(f"Cannot use the proxy: {exc}") from exc
            except OSError as exc:
                # Nothing here touches the disk except the TLS material, and
                # only when the environment is trusted and verify_ssl is
                # True: httpx reads SSL_CERT_FILE and SSL_CERT_DIR itself,
                # and a missing file arrives as FileNotFoundError and one
                # holding no certificate as ssl.SSLError, both OSError.
                raise ConfigurationError(
                    f"Cannot verify TLS against what the environment names "
                    f"in SSL_CERT_FILE or SSL_CERT_DIR: {exc}"
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
    ) -> PiKVMWebSocket:
        """Create a WebSocket connection.

        The socket authenticates with the *user* and *passwd* this client was
        built with; it does not use :attr:`cookies`.

        Args:
            stream: Count this client as a video viewer, which is also
                kvmd's own default. kvmd runs the streamer while at least one
                connected session asked for it, so a socket opened with
                ``False`` lets the video pipeline stop — and
                :meth:`StreamerResource.snapshot` then answers HTTP 503
                unless something else is watching. Pass ``False`` only for a
                client that reads events and never looks at the picture.
            binary: Send HID input over kvmd's binary channel instead of as
                JSON events, the way kvmd's own web UI does. Both reach the
                same handlers; see :class:`PiKVMWebSocket`.
            open_timeout: Timeout for opening the connection (defaults to
                the client *timeout*).
            close_timeout: Timeout for closing the connection (defaults to
                the client *timeout*).

        Returns:
            A *PiKVMWebSocket* async context manager. It inherits this
            client's *verify_ssl*, *proxy*, *trust_env* and
            *follow_redirects*, and this client's *timeout* stands in for
            whichever of the two timeouts below was left out; with an
            external *http_client* it still uses the credentials and URL
            passed to this constructor, since it does not go through httpx
            at all.

        Raises:
            ConfigurationError: If this client has been closed, or the URL it
                was built with has no usable scheme.
        """
        if self._closed:
            raise ConfigurationError(
                "This PiKVM client has been closed; it cannot open a new "
                "WebSocket. Build a new client."
            )
        return PiKVMWebSocket(
            url=self._url,
            user=self._user,
            passwd=self._password,
            verify_ssl=self._verify_ssl,
            proxy=self._proxy,
            trust_env=self._trust_env,
            stream=stream,
            binary=binary,
            follow_redirects=self._follow_redirects,
            open_timeout=open_timeout if open_timeout is not None else self._timeout,
            close_timeout=close_timeout if close_timeout is not None else self._timeout,
        )
