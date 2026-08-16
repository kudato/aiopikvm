"""Exception hierarchy for aiopikvm.

The bottom of this module also holds the mapping from an HTTP status to the
exception that reports it. Both transports need it — the REST client for a
response and the WebSocket client for a refused upgrade, which kvmd answers
with an ordinary HTTP response — and a status they disagree about is a bug in
whichever one a caller is not looking at.
"""

import json
from typing import Any


class PiKVMError(Exception):
    """Base exception for all aiopikvm errors."""


class ConfigurationError(PiKVMError):
    """The client cannot use what it was given.

    Raised for a base URL without a usable scheme and for credentials that
    cannot be put into HTTP headers.
    """


class APIError(PiKVMError):
    """PiKVM refused the request or answered with something unusable.

    Attributes:
        status_code: HTTP status code (``0`` when parsed from the JSON body).
        error: kvmd's exception class name, e.g. ``"AtxIsBusyError"``. Empty
            when the response carried no kvmd error block.
        error_msg: kvmd's human-readable message, e.g. ``"Performing another
            ATX operation, please try again later"``. Empty when the response
            carried no kvmd error block.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        *,
        error: str = "",
        error_msg: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.error_msg = error_msg


class AuthError(APIError):
    """Authentication failed (HTTP 401/403)."""


class BusyError(APIError):
    """PiKVM is busy with another operation (HTTP 409).

    Raised by ATX, MSD and GPIO calls while an earlier operation is still
    running; the same call usually succeeds once it finishes. ``error`` names
    the subsystem's own kvmd class, such as ``"AtxIsBusyError"``.
    """


class UnavailableError(APIError):
    """The subsystem is offline (HTTP 503).

    Raised for a subsystem that cannot serve the request right now — an MSD
    that has not finished setting up, or a snapshot while the video source
    has no signal. A subsystem switched off in the kvmd config answers
    HTTP 400 instead, so that arrives as a plain :class:`APIError` whose
    ``error`` names the reason, e.g. ``"AtxDisabledError"``.
    """


class RedirectError(APIError):
    """PiKVM answered with a redirect (HTTP 3xx).

    kvmd redirects doubled and trailing slashes, and PiKVM's nginx redirects
    ``http://`` to ``https://``. Following those silently would resend the
    credentials to wherever the redirect points, so the client reports them
    instead unless it was created with ``follow_redirects=True``.
    """


class ResponseError(APIError):
    """PiKVM answered with a payload the client could not parse.

    Either the body was not the JSON envelope the API is documented to
    return, or it did not match the model describing that endpoint — which
    usually means a kvmd version this release does not know about yet.
    """


class ConnectError(PiKVMError):
    """Failed to connect to PiKVM, or the connection broke mid-request."""


class ConnectionTimeoutError(PiKVMError):
    """Request to PiKVM timed out."""


class WebSocketError(PiKVMError):
    """WebSocket connection error."""


_STATUS_ERRORS: dict[int, type[APIError]] = {
    401: AuthError,
    403: AuthError,
    409: BusyError,
    503: UnavailableError,
}
"""kvmd maps IsBusyError to 409 and UnavailableError to 503 (htserver.py)."""


def _status_error(
    status: int,
    *,
    error: str = "",
    error_msg: str = "",
    detail: str = "",
    location: str = "",
) -> APIError:
    """Build the exception reporting an HTTP status kvmd refused with.

    Args:
        status: HTTP status code. Any status a caller could not use: 3xx and
            above over REST, and over the WebSocket anything but 101 —
            including a 2xx from something that did not understand the
            upgrade at all.
        error: kvmd's exception class name from the response envelope.
        error_msg: kvmd's human-readable message from the envelope.
        detail: Fallback description when the envelope carried neither — the
            start of the body, or the reason phrase.
        location: Target of a redirect, when the response carried one.

    Returns:
        :class:`RedirectError` for 3xx, the class registered for the status,
        or :class:`APIError` for anything else.
    """
    if 300 <= status < 400:
        return RedirectError(
            f"HTTP {status}: PiKVM redirected to "
            f"{location or 'an undisclosed location'}. Point the client at "
            "the final URL, or pass follow_redirects=True to follow it "
            "and resend the credentials there.",
            status,
        )

    described = error_msg or error or detail
    return _STATUS_ERRORS.get(status, APIError)(
        f"HTTP {status}: {described}" if described else f"HTTP {status}",
        status,
        error=error,
        error_msg=error_msg,
    )


def _error_fields(body: Any) -> tuple[str, str]:
    """Extract kvmd's error block from a parsed response body.

    kvmd reports failures as ``{"ok": false, "result": {"error": "<class>",
    "error_msg": "<message>"}}`` — over REST, and equally in the body of an
    upgrade it refuses.

    Args:
        body: The parsed response body, or anything else when it could not
            be parsed.

    Returns:
        The ``(error, error_msg)`` pair, each empty when the body is not a
        kvmd error envelope.
    """
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return ("", "")
    error = result.get("error")
    error_msg = result.get("error_msg")
    return (
        error if isinstance(error, str) else "",
        error_msg if isinstance(error_msg, str) else "",
    )


def _error_fields_from_bytes(body: bytes | bytearray) -> tuple[str, str]:
    """Extract kvmd's error block from a raw response body.

    Args:
        body: Raw response body, empty when the server sent none.

    Returns:
        The ``(error, error_msg)`` pair, each empty when the body is not a
        kvmd error envelope.
    """
    try:
        return _error_fields(json.loads(body))
    except (ValueError, UnicodeDecodeError):
        return ("", "")
