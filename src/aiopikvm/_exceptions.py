"""Exception hierarchy for aiopikvm."""


class PiKVMError(Exception):
    """Base exception for all aiopikvm errors."""


class APIError(PiKVMError):
    """PiKVM API returned an error.

    Attributes:
        status_code: HTTP status code (``0`` when parsed from the JSON body).
    """

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(APIError):
    """Authentication failed (HTTP 401/403)."""


class ConnectError(PiKVMError):
    """Failed to connect to PiKVM."""


class ConnectionTimeoutError(PiKVMError):
    """Request to PiKVM timed out."""


class WebSocketError(PiKVMError):
    """WebSocket connection error."""
