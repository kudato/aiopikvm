"""aiopikvm — async Python client for PiKVM API."""

from aiopikvm._client import PiKVM
from aiopikvm._exceptions import (
    APIError,
    AuthError,
    ConnectError,
    ConnectionTimeoutError,
    PiKVMError,
    WebSocketError,
)
from aiopikvm._ws import PiKVMWebSocket
from aiopikvm.models.atx import ATXLeds, ATXState
from aiopikvm.models.gpio import GPIOChannel, GPIOInput, GPIOState
from aiopikvm.models.hid import HIDKeyboard, HIDKeymap, HIDMouse, HIDState
from aiopikvm.models.msd import MSDDrive, MSDState, MSDStorage
from aiopikvm.models.streamer import (
    OCRResult,
    Resolution,
    StreamerSource,
    StreamerState,
)
from aiopikvm.models.switch import EDID, SwitchPort, SwitchState

__version__ = "0.1.1"

__all__ = [
    "EDID",
    "APIError",
    "ATXLeds",
    "ATXState",
    "AuthError",
    "ConnectError",
    "ConnectionTimeoutError",
    "GPIOChannel",
    "GPIOInput",
    "GPIOState",
    "HIDKeyboard",
    "HIDKeymap",
    "HIDMouse",
    "HIDState",
    "MSDDrive",
    "MSDState",
    "MSDStorage",
    "OCRResult",
    "PiKVM",
    "PiKVMError",
    "PiKVMWebSocket",
    "Resolution",
    "StreamerSource",
    "StreamerState",
    "SwitchPort",
    "SwitchState",
    "WebSocketError",
    "__version__",
]
