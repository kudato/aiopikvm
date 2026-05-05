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
    OCRInfo,
    OCRLangs,
    Resolution,
    Streamer,
    StreamerEncoder,
    StreamerFeatures,
    StreamerH264,
    StreamerLimitRange,
    StreamerLimits,
    StreamerParams,
    StreamerSinkInfo,
    StreamerSinks,
    StreamerSnapshot,
    StreamerSource,
    StreamerState,
    StreamerStream,
)
from aiopikvm.models.switch import EDID, SwitchPort, SwitchState

__version__ = "0.2.1"

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
    "OCRInfo",
    "OCRLangs",
    "PiKVM",
    "PiKVMError",
    "PiKVMWebSocket",
    "Resolution",
    "Streamer",
    "StreamerEncoder",
    "StreamerFeatures",
    "StreamerH264",
    "StreamerLimitRange",
    "StreamerLimits",
    "StreamerParams",
    "StreamerSinkInfo",
    "StreamerSinks",
    "StreamerSnapshot",
    "StreamerSource",
    "StreamerState",
    "StreamerStream",
    "SwitchPort",
    "SwitchState",
    "WebSocketError",
    "__version__",
]
