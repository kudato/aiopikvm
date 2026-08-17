"""HID API — keyboard, mouse, text input."""

import re
from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError
from aiopikvm.models.hid import HIDKeymaps, HIDState, _HIDInactivity

_SPLIT_CHARS = re.compile(r"[,\t ]")
"""What kvmd splits a shortcut on, from its ``valid_string_list`` default."""

KEY_NAMES = frozenset({
    "AltLeft", "AltRight", "ArrowDown", "ArrowLeft", "ArrowRight",
    "ArrowUp", "AudioVolumeDown", "AudioVolumeMute", "AudioVolumeUp",
    "Backquote", "Backslash", "Backspace", "BracketLeft", "BracketRight",
    "CapsLock", "Comma", "ContextMenu", "ControlLeft", "ControlRight",
    "Convert", "Delete", "Digit0", "Digit1", "Digit2", "Digit3", "Digit4",
    "Digit5", "Digit6", "Digit7", "Digit8", "Digit9", "End", "Enter",
    "Equal", "Escape", "F1", "F10", "F11", "F12", "F2", "F20", "F3", "F4",
    "F5", "F6", "F7", "F8", "F9", "Home", "Insert", "IntlBackslash",
    "IntlRo", "IntlYen", "KanaMode", "KeyA", "KeyB", "KeyC", "KeyD", "KeyE",
    "KeyF", "KeyG", "KeyH", "KeyI", "KeyJ", "KeyK", "KeyL", "KeyM", "KeyN",
    "KeyO", "KeyP", "KeyQ", "KeyR", "KeyS", "KeyT", "KeyU", "KeyV", "KeyW",
    "KeyX", "KeyY", "KeyZ", "MetaLeft", "MetaRight", "Minus", "NonConvert",
    "NumLock", "Numpad0", "Numpad1", "Numpad2", "Numpad3", "Numpad4",
    "Numpad5", "Numpad6", "Numpad7", "Numpad8", "Numpad9", "NumpadAdd",
    "NumpadDecimal", "NumpadDivide", "NumpadEnter", "NumpadMultiply",
    "NumpadSubtract", "PageDown", "PageUp", "Pause", "Period", "Power",
    "PrintScreen", "Quote", "ScrollLock", "Semicolon", "ShiftLeft",
    "ShiftRight", "Slash", "Space", "Tab",
})  # fmt: skip
"""Every key name kvmd accepts, matched case-sensitively.

These are the keys of kvmd's ``WEB_TO_EVDEV`` table, which is where its
validator looks: the names a browser puts in ``KeyboardEvent.code``, which
is why they read like ``"KeyA"`` and ``"Digit1"`` rather than ``"a"`` and
``"1"``. Anything else is refused — ``"keya"`` and ``"a"`` included.

Only one of the two transports says so. An HTTP call raises
:class:`APIError` with HTTP 400 and kvmd's own message naming the key it
would not take; a key sent over the WebSocket is dropped inside kvmd's
handler with no answer of any kind, and nothing there tells a typo from a
keystroke that landed. Checking a name that came from somewhere untrusted
is what stands in for the answer the socket does not give::

    if key not in KEY_NAMES:
        raise ValueError(f"kvmd has no key named {key!r}")
    await ws.send_key(key, state=True)

The set is kvmd 4.186's, recorded from the device behind this project's
fixtures; no endpoint exposes the table, so this cannot be read from a
device at runtime. Another version may know more names — nothing in the
client enforces the set, and a name outside it is sent as given.
"""


class HIDResource(BaseResource):
    """HID keyboard and mouse control for PiKVM."""

    async def get_state(self) -> HIDState:
        """Get the current HID state.

        Returns:
            Current HID subsystem state.
        """
        return await self._get_model("/api/hid", HIDState)

    async def get_inactivity(self) -> int:
        """Get the time since the last keyboard or mouse event.

        The counter is what drives the jiggler. It tracks the input kvmd
        itself delivered, from any of its clients — somebody typing on a
        keyboard plugged straight into the target host does not reset it.

        Returns:
            Seconds since the last HID event kvmd sent.
        """
        state = await self._get_model("/api/hid/inactivity", _HIDInactivity)
        return state.inactivity

    async def set_params(
        self,
        *,
        keyboard_output: str | None = None,
        mouse_output: str | None = None,
        jiggler: bool | None = None,
    ) -> None:
        """Set HID output parameters.

        Args:
            keyboard_output: Keyboard output type. Valid values are the ones
                ``HIDState.keyboard.outputs.available`` lists.
            mouse_output: Mouse output type, e.g. ``"usb"`` (absolute) or
                ``"usb_rel"`` (relative); see
                ``HIDState.mouse.outputs.available``.
            jiggler: Whether the mouse jiggler moves the pointer while the
                host is idle.
        """
        params: dict[str, str | int] = {}
        if keyboard_output is not None:
            params["keyboard_output"] = keyboard_output
        if mouse_output is not None:
            params["mouse_output"] = mouse_output
        if jiggler is not None:
            params["jiggler"] = int(jiggler)
        await self._post("/api/hid/set_params", params=params)

    async def set_connected(self, connected: bool) -> None:
        """Unplug the emulated HID from the target host, or plug it back in.

        Only the MCU-based backends do this: kvmd 4.186 implements it in the
        ones that drive a separate microcontroller, ``hid.type`` set to
        ``serial`` or ``spi``, and nowhere else. Under ``otg``, ``ch9329`` or
        ``bt`` the call reaches a base implementation that discards its
        argument, so kvmd answers 200 and nothing happens. The capture device
        behind this project's fixtures, a v3, runs ``otg``.

        Nothing in the response says which of the two took place, so read
        ``HIDState.connected`` — but read it as the one-way signal it is. A
        ``bool`` there is a backend that does implement this call. ``None``
        is not the opposite: an MCU backend reports ``None`` as well until
        its microcontroller has answered with a status word that carries the
        flag, so a board that is merely offline, or whose firmware answers
        the shorter pong, looks exactly like one that cannot unplug
        anything. ``HIDState.online`` rules out the offline board; the
        firmware that never sends the flag is not distinguishable at all.

        Give the change a moment before reading it back, too: it travels to
        the microcontroller through a queue and this call returns as soon as
        it is queued. On the way in it empties that queue, so keystrokes sent
        a moment earlier and not yet delivered are dropped with it.

        :meth:`reset` is a different matter: every backend overrides that.

        Args:
            connected: Whether the host should see the HID as plugged in.
        """
        await self._post("/api/hid/set_connected", params={"connected": int(connected)})

    async def reset(self) -> None:
        """Reset the HID subsystem.

        Every backend overrides this, unlike :meth:`set_connected`, but what
        it means differs by more than the name suggests. Under ``otg`` kvmd
        drops the input still queued and releases every key and button the
        host sees as held — the way out of a modifier left stuck by a script
        that died mid-shortcut. ``bt`` does that and then drops its Bluetooth
        clients, unpairing them unless ``unpair_on_close`` is turned off, so
        the host has to pair again. An MCU backend resets the microcontroller
        itself, through its reset pin where one is configured, and keeps the
        queued input to deliver afterwards. Under ``ch9329`` nothing happens
        that anything can observe: the reset request its loop would send is
        commented out in kvmd 4.186, and all it does instead is set an
        internal busy flag that ``get_state()`` never reports.
        """
        await self._post("/api/hid/reset")

    async def get_keymaps(self) -> HIDKeymaps:
        """Get the keyboard layouts installed on the device.

        Returns:
            The available layout names and the device-wide default.
        """
        result = await self._get("/api/hid/keymaps")
        keymaps = result.get("keymaps") if isinstance(result, dict) else None
        return self._validate(HIDKeymaps, keymaps, "/api/hid/keymaps")

    async def type_text(
        self,
        text: str,
        *,
        limit: int = 0,
        keymap: str | None = None,
        delay: float | None = None,
        slow: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Type text via HID keyboard.

        Args:
            text: Text string to type.
            limit: Server-side truncation: kvmd types the first ``limit``
                characters and silently discards the rest. ``0`` disables it.
                Always sent on the wire — kvmd's own default is 1024, so
                omitting the parameter would cap long strings.
            keymap: Layout used to translate the text into key events, from
                :meth:`get_keymaps`. Defaults to the device-wide layout, which
                is not necessarily ``en-us``.
            delay: Seconds to sleep between key events, 0 to 5. Defaults to
                ``0.02`` when ``slow`` is set and to ``0`` otherwise.
            slow: Enable server-side per-character delays for reliable input.
            timeout: Per-call timeout in seconds. kvmd types the whole string
                before answering, so anything that stretches that out needs a
                wider timeout than the client default: ``slow``, a large
                ``delay``, or simply a long string, which no longer stops at
                the first 1024 characters.
        """
        params: dict[str, str | int | float] = {"limit": limit}
        if keymap is not None:
            params["keymap"] = keymap
        if delay is not None:
            params["delay"] = delay
        if slow:
            params["slow"] = 1
        await self._post(
            "/api/hid/print",
            content=text.encode(),
            headers={"Content-Type": "text/plain"},
            params=params,
            timeout=timeout,
        )

    async def send_key(self, key: str, *, state: bool | None = None) -> None:
        """Send a single key event.

        Args:
            key: Key name, one of :data:`KEY_NAMES` and matched
                case-sensitively.
            state: Key state (``True`` = press, ``False`` = release,
                ``None`` = press and release).

        Raises:
            APIError: If kvmd has no key by that name (HTTP 400).
        """
        params: dict[str, Any] = {"key": key}
        if state is not None:
            params["state"] = int(state)
        await self._post("/api/hid/events/send_key", params=params)

    async def send_shortcut(self, *keys: str) -> None:
        """Send a keyboard shortcut.

        The server presses the keys in order and releases them in
        reverse order, with a fixed 50 ms delay between events.

        Args:
            *keys: Key names forming the shortcut, each one of
                :data:`KEY_NAMES` and matched case-sensitively.

        Raises:
            ConfigurationError: If no keys are given, or if one of them is
                empty or holds a comma, a space or a tab. kvmd takes the
                shortcut as one string and splits it on exactly those
                characters, throwing away what falls out empty, so such a key
                would not survive the trip — an empty one vanishes and the
                rest of the shortcut is pressed as if it had never been
                asked for. No name in :data:`KEY_NAMES` contains any of them.
            APIError: If kvmd has no key by one of those names (HTTP 400).
                It validates the whole list before pressing anything, so a
                shortcut with one bad name sends nothing at all.
        """
        if not keys:
            raise ConfigurationError("send_shortcut() requires at least one key")
        for key in keys:
            if not key or _SPLIT_CHARS.search(key):
                raise ConfigurationError(
                    f"{key!r} cannot be part of a shortcut: kvmd splits the "
                    "list on commas, spaces and tabs, and drops what is empty"
                )
        # The PiKVM endpoint reads `keys` as a single comma-separated value;
        # passing a list makes httpx send repeated params (keys=A&keys=B) and
        # the server keeps only the first key.
        await self._post(
            "/api/hid/events/send_shortcut",
            params={"keys": ",".join(keys)},
        )

    async def send_mouse_button(
        self, button: str, *, state: bool | None = None
    ) -> None:
        """Send a mouse button event.

        Args:
            button: Button name (e.g. ``"left"``, ``"right"``).
            state: Button state (``True`` = press, ``False`` = release,
                ``None`` = click).
        """
        params: dict[str, Any] = {"button": button}
        if state is not None:
            params["state"] = int(state)
        await self._post("/api/hid/events/send_mouse_button", params=params)

    async def send_mouse_move(self, to_x: int, to_y: int) -> None:
        """Move the mouse to absolute coordinates.

        Args:
            to_x: Target X coordinate.
            to_y: Target Y coordinate.
        """
        await self._post(
            "/api/hid/events/send_mouse_move",
            params={"to_x": to_x, "to_y": to_y},
        )

    async def send_mouse_relative(self, delta_x: int, delta_y: int) -> None:
        """Move the mouse by relative offset.

        Args:
            delta_x: Horizontal offset.
            delta_y: Vertical offset.
        """
        await self._post(
            "/api/hid/events/send_mouse_relative",
            params={"delta_x": delta_x, "delta_y": delta_y},
        )

    async def send_mouse_wheel(self, delta_x: int, delta_y: int) -> None:
        """Send a mouse wheel event.

        Args:
            delta_x: Horizontal scroll delta.
            delta_y: Vertical scroll delta.
        """
        await self._post(
            "/api/hid/events/send_mouse_wheel",
            params={"delta_x": delta_x, "delta_y": delta_y},
        )
