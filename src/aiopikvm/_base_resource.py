"""Base class for API resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ValidationError

from aiopikvm._exceptions import APIError, ResponseError

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
        data: dict[str, str] | None = None,
        content: bytes | httpx.AsyncByteStream | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        """Send a request and parse the PiKVM JSON envelope.

        Args:
            method: HTTP method.
            path: URL path relative to the PiKVM base URL.
            params: Query parameters.
            json: JSON body.
            data: Form fields, sent as ``application/x-www-form-urlencoded``.
            content: Raw body bytes or async byte stream.
            headers: Extra HTTP headers.
            timeout: Override the client-level timeout for this call.

        Returns:
            The unwrapped ``result`` payload.

        Raises:
            ResponseError: When the body is not the documented JSON envelope.
            APIError: When the ``ok`` field is ``false``.
        """
        response = await self._client.request(
            method,
            path,
            params=params,
            json=json,
            data=data,
            content=content,
            headers=headers,
            timeout=timeout,
        )

        try:
            body = response.json()
        except (ValueError, TypeError) as exc:
            raise ResponseError(
                f"Invalid JSON response from {path}: {response.text[:200]}",
                response.status_code,
            ) from exc

        if not isinstance(body, dict):
            raise ResponseError(
                f"Invalid JSON response from {path}: expected an object, "
                f"got {type(body).__name__}",
                response.status_code,
            )

        if not body.get("ok", False):
            raise _envelope_error(body.get("result"))

        return body.get("result")

    @staticmethod
    def _validate[M: BaseModel](model: type[M], data: Any, path: str) -> M:
        """Validate a payload against a response model.

        Args:
            model: Model describing the payload.
            data: Payload to validate.
            path: URL path it came from, for the error message.

        Returns:
            The validated model.

        Raises:
            ResponseError: If the payload does not match the model. Pydantic
                raises ``ValidationError``, which is outside the aiopikvm
                hierarchy and would escape ``except PiKVMError``.
        """
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise ResponseError(
                f"{path} returned a payload {model.__name__} cannot parse. "
                f"This usually means a kvmd version aiopikvm does not know "
                f"about yet:\n{exc}"
            ) from exc

    async def _get_model[M: BaseModel](
        self,
        path: str,
        model: type[M],
        *,
        params: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> M:
        """Send a GET request and validate the result against a model."""
        result = await self._get(path, params=params, timeout=timeout)
        return self._validate(model, result, path)

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        """Send a GET request and parse the PiKVM response."""
        return await self._request("GET", path, params=params, timeout=timeout)

    async def _post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        content: bytes | httpx.AsyncByteStream | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        """Send a POST request and parse the PiKVM response."""
        return await self._request(
            "POST",
            path,
            params=params,
            json=json,
            data=data,
            content=content,
            headers=headers,
            timeout=timeout,
        )

    async def _delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        """Send a DELETE request and parse the PiKVM response."""
        return await self._request("DELETE", path, params=params, timeout=timeout)

    async def _patch(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> Any:
        """Send a PATCH request and parse the PiKVM response."""
        return await self._request(
            "PATCH", path, params=params, json=json, timeout=timeout
        )

    async def _get_raw(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "application/octet-stream",
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        """Send a GET request and return the raw *httpx.Response*."""
        return await self._client.request(
            "GET",
            path,
            params=params,
            headers={"Accept": accept},
            timeout=timeout,
        )

    async def _post_raw(
        self,
        path: str,
        *,
        data: dict[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        """Send a POST request and return the raw *httpx.Response*.

        For endpoints that answer in the headers rather than the body:
        ``/auth/login`` returns an empty envelope and hands out the session
        token in ``Set-Cookie``.

        Args:
            path: URL path relative to the PiKVM base URL.
            data: Form fields, sent as ``application/x-www-form-urlencoded``.
            timeout: Override the client-level timeout for this call.

        Returns:
            The *httpx.Response* object.
        """
        return await self._client.request("POST", path, data=data, timeout=timeout)


def _envelope_error(result: Any) -> APIError:
    """Build the error for an ``{"ok": false}`` envelope.

    kvmd fills ``result`` with ``{"error": "<class>", "error_msg": "<text>"}``;
    the status code stays ``0`` because the failure was reported in the body
    rather than by the HTTP status.

    Args:
        result: The ``result`` field of the response envelope.

    Returns:
        The exception to raise.
    """
    if isinstance(result, dict):
        error = result.get("error")
        error_msg = result.get("error_msg")
        error = error if isinstance(error, str) else ""
        error_msg = error_msg if isinstance(error_msg, str) else ""
        return APIError(
            error_msg or error or "Unknown error", error=error, error_msg=error_msg
        )
    return APIError(str(result) if result else "Unknown error")
