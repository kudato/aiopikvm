"""PiKVM async client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

import httpx

from aiopikvm._constants import (
    DEFAULT_FOLLOW_REDIRECTS,
    DEFAULT_TIMEOUT,
    DEFAULT_VERIFY_SSL,
)
from aiopikvm._exceptions import (
    APIError,
    AuthError,
    BusyError,
    ConfigurationError,
    ConnectError,
    ConnectionTimeoutError,
    PiKVMError,
    RedirectError,
    UnavailableError,
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

_STATUS_ERRORS: dict[int, type[APIError]] = {
    401: AuthError,
    403: AuthError,
    409: BusyError,
    503: UnavailableError,
}
"""kvmd maps IsBusyError to 409 and UnavailableError to 503 (htserver.py)."""


class PiKVM:
    """Async client for PiKVM API.

    Usage::

        async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
            await kvm.atx.power_on()

    An external *httpx.AsyncClient* can be provided via *http_client*; in that
    case the caller is responsible for closing it.
    """

    def __init__(
        self,
        url: str,
        *,
        user: str = "admin",
        passwd: str = "",
        totp: str | None = None,
        verify_ssl: bool = DEFAULT_VERIFY_SSL,
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
                PiKVM ships a self-signed one.
            timeout: Default per-request timeout in seconds.
            follow_redirects: Follow HTTP redirects instead of raising
                :class:`RedirectError`. Off by default: a redirect resends
                the credential headers to whatever it points at, and the
                usual cause — an ``http://`` base URL that nginx redirects
                to ``https://`` — has already exposed the password in
                cleartext by then.
            http_client: Pre-built httpx client. When given, this client
                does not close it and the arguments above are ignored.
        """
        self._url = url.rstrip("/")
        self._user = user
        self._passwd = passwd
        self._totp = totp
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._external_client = http_client is not None
        self._client: httpx.AsyncClient | None = http_client

    @property
    def _password(self) -> str:
        """Password with optional TOTP code appended."""
        return self._passwd if self._totp is None else f"{self._passwd}{self._totp}"

    # --- HTTP ----------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        """Return the underlying *httpx.AsyncClient*.

        Raises:
            PiKVMError: If the async context has not been entered yet.
        """
        if self._client is None:
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
            PiKVMError: If the async context has not been entered yet.
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
            PiKVMError: If the async context has not been entered yet.
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
            ConfigurationError: The base URL has no usable scheme.
            ConnectError: Connection to PiKVM failed or broke mid-request.
            ConnectionTimeoutError: Request timed out.
            AuthError: Authentication failed (401/403).
            BusyError: PiKVM is busy with another operation (409).
            UnavailableError: The subsystem is disabled or offline (503).
            RedirectError: PiKVM answered with a redirect (3xx) and the
                client was not created with ``follow_redirects=True``.
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
        except httpx.UnsupportedProtocol as exc:
            raise ConfigurationError(
                f"{exc} Pass the scheme in the PiKVM URL, e.g. https://pikvm.local."
            ) from exc
        except httpx.TransportError as exc:
            # Covers ConnectError, ReadError/WriteError and the
            # RemoteProtocolError kvmd raises at every restart, which drops
            # in-flight connections after a one-second shutdown timeout.
            raise ConnectError(str(exc)) from exc

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
            location = response.headers.get("location", "")
            raise RedirectError(
                f"HTTP {status}: PiKVM redirected to "
                f"{location or 'an undisclosed location'}. Point the client at "
                "the final URL, or pass follow_redirects=True to follow it "
                "and resend the credentials there.",
                status,
            )

        error, error_msg = cls._error_fields(response)
        detail = error_msg or error or cls._body_excerpt(response)
        raise _STATUS_ERRORS.get(status, APIError)(
            f"HTTP {status}: {detail}" if detail else f"HTTP {status}",
            status,
            error=error,
            error_msg=error_msg,
        )

    @staticmethod
    def _error_fields(response: httpx.Response) -> tuple[str, str]:
        """Extract kvmd's error block from a response body.

        kvmd reports failures as ``{"ok": false, "result": {"error":
        "<class>", "error_msg": "<message>"}}``.

        Args:
            response: The HTTP response to read.

        Returns:
            The ``(error, error_msg)`` pair, each empty when the body is not
            a kvmd error envelope or has not been read yet.
        """
        try:
            body = response.json()
        except (ValueError, TypeError, httpx.ResponseNotRead):
            return ("", "")
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            return ("", "")
        error = result.get("error")
        error_msg = result.get("error_msg")
        return (
            error if isinstance(error, str) else "",
            error_msg if isinstance(error_msg, str) else "",
        )

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
                client was not created with ``follow_redirects=True``.
            APIError: Server returned any other error status (>= 400).
        """
        client = self._ensure_client()
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
        except httpx.UnsupportedProtocol as exc:
            raise ConfigurationError(
                f"{exc} Pass the scheme in the PiKVM URL, e.g. https://pikvm.local."
            ) from exc
        except httpx.TransportError as exc:
            raise ConnectError(str(exc)) from exc

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
        if self._client is None:
            try:
                self._client = httpx.AsyncClient(
                    base_url=self._url,
                    headers={
                        "X-KVMD-User": self._user,
                        "X-KVMD-Passwd": self._password,
                    },
                    verify=self._verify_ssl,
                    timeout=self._timeout,
                    follow_redirects=self._follow_redirects,
                )
            except UnicodeEncodeError as exc:
                raise ConfigurationError(
                    f"PiKVM credentials travel in HTTP headers and must be ASCII: {exc}"
                ) from exc
            except httpx.InvalidURL as exc:
                raise ConfigurationError(
                    f"Invalid PiKVM URL {self._url!r}: {exc}"
                ) from exc
        return self

    async def aclose(self) -> None:
        """Close the client and release resources."""
        for name in _RESOURCE_NAMES:
            self.__dict__.pop(name, None)

        if not self._external_client and self._client is not None:
            await self._client.aclose()
            self._client = None

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
            open_timeout: Timeout for opening the connection (defaults to
                the client *timeout*).
            close_timeout: Timeout for closing the connection (defaults to
                the client *timeout*).

        Returns:
            A *PiKVMWebSocket* async context manager.
        """
        return PiKVMWebSocket(
            url=self._url,
            user=self._user,
            passwd=self._password,
            verify_ssl=self._verify_ssl,
            stream=stream,
            open_timeout=open_timeout if open_timeout is not None else self._timeout,
            close_timeout=close_timeout if close_timeout is not None else self._timeout,
        )
