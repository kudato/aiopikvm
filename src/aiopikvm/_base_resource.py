"""Base class for API resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from aiopikvm._exceptions import APIError

if TYPE_CHECKING:
    from aiopikvm._client import PiKVM


class BaseResource:
    """Base class for all PiKVM API resources.

    Provides convenience methods that delegate HTTP work to
    :pymethod:`PiKVM.request` and parse the standard PiKVM response
    envelope ``{"ok": true, "result": {...}}``.
    """

    __slots__ = ("_client",)

    def __init__(self, client: PiKVM) -> None:
        self._client = client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | httpx.AsyncByteStream | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a request and parse the PiKVM JSON envelope.

        Raises:
            APIError: When the ``ok`` field is ``false``.
        """
        response = await self._client.request(
            method, path, params=params, json=json, content=content, headers=headers
        )

        try:
            body = response.json()
        except (ValueError, TypeError) as exc:
            raise APIError(f"Invalid JSON response: {response.text[:200]}") from exc

        if not body.get("ok", False):
            result = body.get("result", {})
            if isinstance(result, dict):
                msg = result.get("error", "Unknown error")
            else:
                msg = str(result) if result else "Unknown error"
            raise APIError(msg)

        return body.get("result")

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Send a GET request and parse the PiKVM response."""
        return await self._request("GET", path, params=params)

    async def _post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        content: bytes | httpx.AsyncByteStream | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a POST request and parse the PiKVM response."""
        return await self._request(
            "POST", path, params=params, json=json, content=content, headers=headers
        )

    async def _delete(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Send a DELETE request and parse the PiKVM response."""
        return await self._request("DELETE", path, params=params)

    async def _patch(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Send a PATCH request and parse the PiKVM response."""
        return await self._request("PATCH", path, params=params, json=json)

    async def _get_raw(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "application/octet-stream",
    ) -> httpx.Response:
        """Send a GET request and return the raw *httpx.Response*."""
        return await self._client.request(
            "GET", path, params=params, headers={"Accept": accept}
        )
