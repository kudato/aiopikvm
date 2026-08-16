"""Exception hierarchy for aiopikvm."""


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
