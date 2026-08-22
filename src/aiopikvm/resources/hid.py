"""HID API — keyboard, mouse, text input."""

import re
from typing import Any, Literal

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError
from aiopikvm.models.hid import HIDKeymaps, HIDState, _HIDInactivity

_LOST_IN_A_SHORTCUT = re.compile(r"[,\s]")
"""What a shortcut key cannot contain and still arrive as itself.

kvmd takes the list as one string, strips it, splits it on ``[,\\t ]+`` and
drops what comes out empty; then its key validator strips each item again.
That is why this is every whitespace character and not only the two the
split names — a key of ``"\\n"`` is trimmed away like an empty one, and a
padded ``"KeyA\\r"`` arrives as a different key than the one asked for.
"""

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
[`APIError`][aiopikvm.APIError] with HTTP 400, and its message names the key
kvmd would not take — except from
[`HIDResource.send_key()`][aiopikvm.resources.hid.HIDResource.send_key], where
a name past 16 characters is refused on length alone and the message names
nothing at all. A key sent over the WebSocket is dropped inside kvmd's handler
with no answer of any kind, and nothing there tells a typo from a keystroke
that landed. Checking a name that came from somewhere untrusted is what stands
in for the answer the socket does not give:

    if key not in KEY_NAMES:
        raise ValueError(f"kvmd has no key named {key!r}")
    await ws.send_key(key, state=True)

The set is kvmd 4.186's, recorded from the device behind this project's
fixtures; no endpoint exposes the table, so this cannot be read from a
device at runtime. Another version may know more names — nothing in the
client enforces the set, and a name outside it is sent as given.

This is a runtime set rather than a type, unlike the smaller vocabularies
below. A key name is usually computed — read out of a browser event, a
config file, a table of shortcuts — and a static list of 115 members would
be in the way far more often than it caught a typo.
"""

type KeyboardOutput = Literal["usb", "ps2", "disabled"]
"""What ``keyboard_output`` may be in
[`HIDResource.set_params()`][aiopikvm.resources.hid.HIDResource.set_params].

These three are what kvmd's own validator accepts; anything else is
HTTP 400. It lowercases the value first, so a device would also take
``"USB"`` — only the canonical spelling is typed here.

Being accepted is not being applied. kvmd validates against this list
whatever HID backend is running, and *then* hands the value to a backend
that may have no use for it. In kvmd 4.186 only the MCU backends act on
it at all; ``otg``, ``ch9329`` and ``bt`` discard the argument and still
answer 200. ``HIDState.keyboard.outputs.available`` is what the running
backend offers, and it is empty when there is no choice to make.
"""

type MouseOutput = Literal["usb", "usb_win98", "usb_rel", "ps2", "disabled"]
"""What ``mouse_output`` may be in
[`HIDResource.set_params()`][aiopikvm.resources.hid.HIDResource.set_params].

``"usb"`` is the absolute mouse, ``"usb_rel"`` the relative one, and
``"usb_win98"`` an absolute mouse with a workaround for Windows 98's driver.
``HIDState.mouse.outputs.active`` names the one in use, and
``HIDState.mouse.absolute`` says whether it reports positions or movement —
which is what decides between
[`HIDResource.send_mouse_move()`][aiopikvm.resources.hid.HIDResource.send_mouse_move]
and
[`HIDResource.send_mouse_relative()`][aiopikvm.resources.hid.HIDResource.send_mouse_relative].

The same two-step as
[`KeyboardOutput`][aiopikvm.resources.hid.KeyboardOutput]: kvmd validates the
name against this list on every backend, then hands it to a backend that may
not have that mouse. What happens then is the backend's own business and not
always visible — ``otg`` ignores a name outside
``HIDState.mouse.outputs.available``, under an HTTP 200, while ``ch9329``
offers two names and acts on any of the five, taking everything but ``"usb"``
as its relative mouse. Read the state back rather than assume the name was
applied as asked.
"""

type MouseButton = Literal["left", "right", "middle", "up", "down"]
"""The mouse buttons kvmd knows, over REST and over the WebSocket alike.

``"up"`` and ``"down"`` are the side buttons a browser reports as back and
forward — not wheel directions, which are
[`HIDResource.send_mouse_wheel()`][aiopikvm.resources.hid.HIDResource.send_mouse_wheel].
kvmd lowercases the name before it looks it up, so only the canonical spelling
is typed.

This is the only one of these vocabularies with two ways in, and they report a
wrong name differently:
[`HIDResource.send_mouse_button()`][aiopikvm.resources.hid.HIDResource.send_mouse_button]
raises [`APIError`][aiopikvm.APIError] with HTTP 400, while
``PiKVMWebSocket.send_mouse_button()`` gets no answer of any kind — the frame
is dropped inside kvmd's handler, as a bad key name is.
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
        keyboard_output: KeyboardOutput | None = None,
        mouse_output: MouseOutput | None = None,
        jiggler: bool | None = None,
    ) -> None:
        """Set HID output parameters.

        Args:
            keyboard_output: Keyboard output type, one of
                [`KeyboardOutput`][aiopikvm.resources.hid.KeyboardOutput].
                Which of them the running backend can actually switch to is
                ``HIDState.keyboard.outputs.available``.
            mouse_output: Mouse output type, one of
                [`MouseOutput`][aiopikvm.resources.hid.MouseOutput] —
                ``"usb"`` for the absolute mouse, ``"usb_rel"`` for the
                relative one; see ``HIDState.mouse.outputs.available``.
            jiggler: Whether the mouse jiggler moves the pointer while the
                host is idle.

        Raises:
            APIError: If kvmd does not know one of these output names
                (HTTP 400). An output it knows but the running backend does
                not offer is *not* an error — it answers 200 and what
                becomes of the name is up to the backend — so read the
                state back to see what took.
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

        [`reset()`][aiopikvm.resources.hid.HIDResource.reset] is a different
        matter: every backend overrides that.

        Args:
            connected: Whether the host should see the HID as plugged in.
        """
        await self._post("/api/hid/set_connected", params={"connected": int(connected)})

    async def reset(self) -> None:
        """Reset the HID subsystem.

        Every backend overrides this, unlike
        [`set_connected()`][aiopikvm.resources.hid.HIDResource.set_connected],
        but what it means differs by more than the name suggests. Under
        ``otg`` kvmd drops the input still queued and releases every key and
        button the host sees as held — the way out of a modifier left stuck by
        a script that died mid-shortcut. ``bt`` does that and then drops its
        Bluetooth clients, unpairing them unless ``unpair_on_close`` is turned
        off, so the host has to pair again. An MCU backend resets the
        microcontroller itself, through its reset pin where one is configured,
        and keeps the queued input to deliver afterwards. Under ``ch9329``
        nothing happens that anything can observe: the reset request its loop
        would send is commented out in kvmd 4.186, and all it does instead is
        set an internal busy flag that ``get_state()`` never reports.
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
                [`get_keymaps()`][aiopikvm.resources.hid.HIDResource.get_keymaps].
                Defaults to the device-wide layout, which is not necessarily
                ``en-us``.
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

    async def send_key(
        self, key: str, *, state: bool | None = None, finish: bool | None = None
    ) -> None:
        """Send a single key event.

        Args:
            key: Key name, one of
                [`KEY_NAMES`][aiopikvm.resources.hid.KEY_NAMES] and matched
                case-sensitively.
            state: Key state (``True`` = press, ``False`` = release,
                ``None`` = press carrying *finish*, which is what kvmd 4.33
                and after do for an event that names no state of its own —
                so a modifier passed this way is pressed and stays down).
            finish: Ask kvmd to release the key in the same event that
                pressed it, so a script that dies mid-keystroke leaves
                nothing held. It goes out only on a press: kvmd reads it
                beside *state* and acts on it only when that is one, so a
                release is sent as a plain release. Not every key is
                released — kvmd exempts the eight modifiers, ``Shift``,
                ``Control``, ``Alt`` and ``Meta``, left and right, along
                with ``PrintScreen``, and presses those and leaves them held
                with no error to say so. A kvmd older than 4.33 has no such
                parameter and does that to every key. The HID guide has the
                exact names and the versions behind them.

                ``None`` asks for nothing and is not the same as ``False``
                everywhere: a *state* of ``None`` is finished by kvmd 4.33
                and after whatever this says, so ``False`` there names a
                behaviour no request can bring about. Pass ``state=True`` to
                press a key and leave it down.

        Raises:
            APIError: If kvmd has no key by that name (HTTP 400).
        """
        params: dict[str, Any] = {"key": key}
        if state is not None:
            params["state"] = int(state)
            # kvmd reads `finish` beside `state` and acts on it only on a
            # press. Anywhere else it names something kvmd never looks at.
            if state and finish:
                params["finish"] = 1
        await self._post("/api/hid/events/send_key", params=params)

    async def send_shortcut(self, *keys: str) -> None:
        """Send a keyboard shortcut.

        The server presses the keys in order and releases them in
        reverse order, with a fixed 50 ms delay between events.

        Args:
            *keys: Key names forming the shortcut, each one of
                [`KEY_NAMES`][aiopikvm.resources.hid.KEY_NAMES] and matched
                case-sensitively.

        Raises:
            ConfigurationError: If no keys are given, or if one of them is
                empty or holds a comma or any whitespace. kvmd takes the
                shortcut as one string, strips it, splits it on commas, spaces
                and tabs and throws away what falls out empty, so such a key
                would not survive the trip: it vanishes and the rest of the
                shortcut is pressed as if it had never been asked for. No name
                in [`KEY_NAMES`][aiopikvm.resources.hid.KEY_NAMES] contains
                any of those characters.
            APIError: If kvmd has no key by one of those names (HTTP 400).
                It validates the whole list before pressing anything, so a
                shortcut with one bad name sends nothing at all.
        """
        if not keys:
            raise ConfigurationError("send_shortcut() requires at least one key")
        for key in keys:
            if not key or _LOST_IN_A_SHORTCUT.search(key):
                raise ConfigurationError(
                    f"{key!r} cannot be part of a shortcut: kvmd splits the "
                    "list on commas and whitespace, and drops what is empty"
                )
        # The PiKVM endpoint reads `keys` as a single comma-separated value;
        # passing a list makes httpx send repeated params (keys=A&keys=B) and
        # the server keeps only the first key.
        await self._post(
            "/api/hid/events/send_shortcut",
            params={"keys": ",".join(keys)},
        )

    async def send_mouse_button(
        self, button: MouseButton, *, state: bool | None = None
    ) -> None:
        """Send a mouse button event.

        Args:
            button: Button name, one of
                [`MouseButton`][aiopikvm.resources.hid.MouseButton].
            state: Button state (``True`` = press, ``False`` = release,
                ``None`` = click).

        Raises:
            APIError: If kvmd has no button by that name (HTTP 400).
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

        Deltas are steps in kvmd's own range, -127 to 127, clamped by kvmd
        rather than rejected, and carried in the HID wheel field. They are not
        a browser's pixel deltas: a browser reports a scroll-down gesture as a
        positive ``deltaY``, and kvmd's own web UI negates it and sizes it by
        its scroll-rate setting (1 to 25, 5 by default), so the gesture
        reaches the device as ``delta_y = -5``.

        Args:
            delta_x: Horizontal step, -127 to 127. It needs a backend with a
                horizontal wheel behind it, and in kvmd 4.206 only ``otg`` has
                one, while its ``horizontal_wheel`` option is on — the
                default. ``serial``, ``spi``, ``ch9329`` and ``bt`` drop it
                without a word, and which way a positive step pans is not
                settled here.
            delta_y: Vertical step, -127 to 127. Negative scrolls down on a
                host with the usual wheel mapping. ``ch9329`` keeps only the
                sign and sends one detent, a zero counting as negative, so the
                size is lost there.
        """
        await self._post(
            "/api/hid/events/send_mouse_wheel",
            params={"delta_x": delta_x, "delta_y": delta_y},
        )
