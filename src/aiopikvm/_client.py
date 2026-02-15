"""PiKVM async client."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

import httpx

from aiopikvm._constants import DEFAULT_TIMEOUT, DEFAULT_VERIFY_SSL
from aiopikvm._exceptions import (
    APIError,
    AuthError,
    ConnectError,
    ConnectionTimeoutError,
    PiKVMError,
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
)


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
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._user = user
        self._passwd = passwd
        self._totp = totp
        self._verify_ssl = verify_ssl
        self._timeout = timeout
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

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | httpx.AsyncByteStream | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send an HTTP request and return the raw response.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: URL path relative to the PiKVM base URL.
            params: Query parameters.
            json: JSON body.
            content: Raw body bytes or async byte stream.
            headers: Extra HTTP headers.

        Returns:
            The *httpx.Response* object.

        Raises:
            ConnectError: Connection to PiKVM failed.
            ConnectionTimeoutError: Request timed out.
            AuthError: Authentication failed (401/403).
            APIError: Server returned an error status (>= 400).
        """
        client = self._ensure_client()
        try:
            response = await client.request(
                method,
                path,
                params=params,
                json=json,
                content=content,
                headers=headers,
            )
        except httpx.ConnectError as exc:
            raise ConnectError(str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ConnectionTimeoutError(str(exc)) from exc

        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Raise an appropriate exception for HTTP error status codes.

        Args:
            response: The HTTP response to check.

        Raises:
            AuthError: If status code is 401 or 403.
            APIError: If status code is >= 400.
        """
        if response.status_code in (401, 403):
            raise AuthError(
                f"Authentication failed: {response.status_code}",
                status_code=response.status_code,
            )

        if response.status_code >= 400:
            raise APIError(
                f"HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
            )

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

    # --- Context manager -----------------------------------------------

    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._url,
                headers={
                    "X-KVMD-User": self._user,
                    "X-KVMD-Passwd": self._password,
                },
                verify=self._verify_ssl,
                timeout=self._timeout,
            )
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
        stream: int = 0,
        open_timeout: float | None = None,
        close_timeout: float | None = None,
    ) -> PiKVMWebSocket:
        """Create a WebSocket connection.

        Args:
            stream: Stream index (default ``0``).
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
