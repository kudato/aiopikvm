"""HID API — keyboard, mouse, text input."""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.hid import HIDState


class HIDResource(BaseResource):
    """HID keyboard and mouse control for PiKVM."""

    async def get_state(self) -> HIDState:
        """Get the current HID state.

        Returns:
            Current HID subsystem state.
        """
        return await self._get_model("/api/hid", HIDState)

    async def set_params(
        self,
        *,
        keyboard_output: str | None = None,
        mouse_output: str | None = None,
    ) -> None:
        """Set HID output parameters.

        Args:
            keyboard_output: Keyboard output type.
            mouse_output: Mouse output type.
        """
        params: dict[str, str] = {}
        if keyboard_output is not None:
            params["keyboard_output"] = keyboard_output
        if mouse_output is not None:
            params["mouse_output"] = mouse_output
        await self._post("/api/hid/set_params", params=params)

    async def set_connected(self, connected: bool) -> None:
        """Set the HID connection state.

        Args:
            connected: Whether HID should be connected.
        """
        await self._post("/api/hid/set_connected", params={"connected": int(connected)})

    async def reset(self) -> None:
        """Reset the HID subsystem."""
        await self._post("/api/hid/reset")

    async def get_keymaps(self) -> dict[str, Any]:
        """Get available keyboard keymaps.

        Returns:
            Dictionary of available keymaps.
        """
        result: dict[str, Any] = await self._get("/api/hid/keymaps")
        return result

    async def type_text(
        self,
        text: str,
        *,
        limit: int = 0,
        slow: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Type text via HID keyboard.

        Args:
            text: Text string to type.
            limit: Maximum characters per request (``0`` = unlimited).
            slow: Enable server-side per-character delays for reliable input.
            timeout: Per-call timeout in seconds. kvmd types the whole string
                before answering, and ``slow`` adds a delay per character.
        """
        params: dict[str, int] = {}
        if limit > 0:
            params["limit"] = limit
        if slow:
            params["slow"] = 1
        await self._post(
            "/api/hid/print",
            content=text.encode(),
            headers={"Content-Type": "text/plain"},
            params=params if params else None,
            timeout=timeout,
        )

    async def send_key(self, key: str, *, state: bool | None = None) -> None:
        """Send a single key event.

        Args:
            key: Key name.
            state: Key state (``True`` = press, ``False`` = release,
                ``None`` = press and release).
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
            *keys: Key names forming the shortcut.

        Raises:
            ValueError: If no keys are given.
        """
        if not keys:
            raise ValueError("send_shortcut() requires at least one key")
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
