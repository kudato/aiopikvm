"""Switch API — multi-port KVM switch and EDID."""

from typing import Any, Literal

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError, ResponseError
from aiopikvm.models.switch import EDID, SwitchState

type ATXAction = Literal["on", "off", "off_hard", "reset_hard"]
"""The power actions :meth:`SwitchResource.atx_power` takes.

``"on"`` and ``"off"`` are short presses of the power switch, the second
leaving the OS to decide what a press means; ``"off_hard"`` holds it down
instead, and ``"reset_hard"`` presses the reset switch.

Every one of the four is conditional on the power state kvmd reads from
the host's power LED: ``"on"`` acts only on a host it believes to be off,
the other three only on one it believes to be on. A command that fails
the test is dropped and the call still succeeds, so where that LED is
miswired or unread none of these does anything.
:meth:`SwitchResource.atx_click` presses a switch unconditionally.

This is kvmd's ATX vocabulary rather than the switch's own: the same
validator serves ``/api/atx/power``, where :class:`ATXResource` spells each
value out as a method of its own. kvmd lowercases the value before it
looks, so only the canonical spelling is typed.
"""

type ATXButton = Literal["power", "power_long", "reset"]
"""The buttons :meth:`SwitchResource.atx_click` presses.

``"power"`` is a short press and ``"power_long"`` the same switch held down
for several seconds; ``"reset"`` is the other front-panel switch. Unlike
every value of :data:`ATXAction`, none of these looks at what state the
host is in — which is what makes them the way to reach a host whose power
LED kvmd cannot read.

Shared with ``/api/atx/click`` and lowercased on the way in, exactly as
:data:`ATXAction` is.
"""


class SwitchResource(BaseResource):
    """Multi-port KVM switch and EDID management for PiKVM."""

    async def get_state(self) -> SwitchState:
        """Get the current switch state.

        Returns:
            Current switch state with active port and port list.
        """
        return await self._get_model("/api/switch", SwitchState)

    async def set_active(self, port: int | float) -> None:
        """Set the active port.

        Args:
            port: Port number, counted from ``0`` across the whole chain, or
                the 1-based ``unit.port`` form (``2.3``). The ``id`` in the
                state is that same 1-based label: on a chain ``float(port.id)``
                selects the port it belongs to, but on a single unit ``id`` is
                one greater than the index. Addressing by position in
                ``model.ports`` avoids the difference.
        """
        await self._post("/api/switch/set_active", params={"port": port})

    async def get_edids(self) -> dict[str, EDID]:
        """Get the EDID catalogue.

        There is no endpoint of its own for this: the catalogue is part of
        the switch state, and this is a shortcut to it.

        Returns:
            Every stored EDID, keyed by id. ``"default"`` always exists.
        """
        return (await self.get_state()).edids.all

    async def create_edid(self, name: str, data: str) -> str:
        """Store a new EDID.

        Args:
            name: Human-readable name.
            data: EDID blob as an uppercase hex string, 256 or 512 characters
                (128 or 256 bytes).

        Returns:
            The id kvmd generated for it, which is what
            :meth:`set_port_params` takes as ``edid_id``.

        Raises:
            ResponseError: If kvmd does not answer with the new id.
        """
        result = await self._post(
            "/api/switch/edids/create", params={"name": name, "data": data}
        )
        edid_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(edid_id, str):
            raise ResponseError(
                "/api/switch/edids/create did not return the new EDID id"
            )
        return edid_id

    async def change_edid(
        self, edid_id: str, *, name: str | None = None, data: str | None = None
    ) -> None:
        """Rename a stored EDID or replace its contents.

        This edits the EDID itself. Assigning one to a port is
        :meth:`set_port_params` with ``edid_id``.

        Args:
            edid_id: Id of the EDID to change. The built-in ``"default"``
                cannot be edited.
            name: New name, if it should change.
            data: New EDID blob as hex, if it should change.

        Raises:
            ConfigurationError: If neither *name* nor *data* is given — kvmd
                answers such a call with success and changes nothing.
        """
        if name is None and data is None:
            raise ConfigurationError(
                "change_edid() needs a new name or new data; kvmd reports "
                "success for a call that carries neither"
            )
        params: dict[str, str] = {"id": edid_id}
        if name is not None:
            params["name"] = name
        if data is not None:
            params["data"] = data
        await self._post("/api/switch/edids/change", params=params)

    async def remove_edid(self, edid_id: str) -> None:
        """Remove a stored EDID.

        Args:
            edid_id: Id of the EDID to remove. The built-in ``"default"``
                cannot be removed.
        """
        await self._post("/api/switch/edids/remove", params={"id": edid_id})

    async def set_active_prev(self) -> None:
        """Switch to the previous port."""
        await self._post("/api/switch/set_active_prev")

    async def set_active_next(self) -> None:
        """Switch to the next port."""
        await self._post("/api/switch/set_active_next")

    async def set_beacon(
        self,
        state: bool,
        *,
        port: int | float | None = None,
        uplink: int | None = None,
        downlink: int | None = None,
    ) -> None:
        """Light or extinguish one beacon.

        Exactly one target must be given. kvmd checks them in the order
        ``port``, ``uplink``, ``downlink`` and falls through to ``downlink``
        when none is present, which answers 400.

        Args:
            state: Whether the beacon is lit.
            port: Port number, or ``unit.port`` on a chain.
            uplink: Unit whose uplink beacon to control, counted from ``0``.
            downlink: Unit whose downlink beacon to control, counted from
                ``0``.

        Raises:
            ConfigurationError: If not exactly one of *port*, *uplink* or
                *downlink* is given.
        """
        targets = [
            name
            for name, value in (
                ("port", port),
                ("uplink", uplink),
                ("downlink", downlink),
            )
            if value is not None
        ]
        if len(targets) != 1:
            raise ConfigurationError(
                "set_beacon() needs exactly one of port, uplink or downlink, "
                f"got {', '.join(targets) if targets else 'none'}"
            )
        params: dict[str, Any] = {"state": int(state)}
        if port is not None:
            params["port"] = port
        elif uplink is not None:
            params["uplink"] = uplink
        else:
            params["downlink"] = downlink
        await self._post("/api/switch/set_beacon", params=params)

    async def set_port_params(
        self,
        port: int | float,
        *,
        edid_id: str | None = None,
        dummy: bool | None = None,
        name: str | None = None,
        atx_click_power_delay: float | None = None,
        atx_click_power_long_delay: float | None = None,
        atx_click_reset_delay: float | None = None,
    ) -> None:
        """Configure port parameters.

        Args:
            port: Port number (``0``-``19`` or unit.port notation).
            edid_id: EDID profile identifier.
            dummy: Pretend host has display attached.
            name: Port name (ASCII letters and numbers).
            atx_click_power_delay: ATX power click delay in seconds (``0``-``10``).
            atx_click_power_long_delay: ATX long power click delay in seconds
                (``0``-``10``).
            atx_click_reset_delay: ATX reset click delay in seconds (``0``-``10``).
        """
        params: dict[str, Any] = {"port": port}
        if edid_id is not None:
            params["edid_id"] = edid_id
        if dummy is not None:
            params["dummy"] = int(dummy)
        if name is not None:
            params["name"] = name
        if atx_click_power_delay is not None:
            params["atx_click_power_delay"] = atx_click_power_delay
        if atx_click_power_long_delay is not None:
            params["atx_click_power_long_delay"] = atx_click_power_long_delay
        if atx_click_reset_delay is not None:
            params["atx_click_reset_delay"] = atx_click_reset_delay
        await self._post("/api/switch/set_port_params", params=params)

    async def set_colors(
        self,
        *,
        inactive: str | None = None,
        active: str | None = None,
        flashing: str | None = None,
        beacon: str | None = None,
        bootloader: str | None = None,
    ) -> None:
        """Set the indicator colours, one per port role.

        Every colour is ``RRGGBB:BB:IIII`` in hex — colour, brightness and
        blink interval in milliseconds — or the string ``"default"`` to go
        back to the built-in value. Roles left out keep their current colour.

        Args:
            inactive: Ports that are not selected.
            active: The selected port.
            flashing: A port whose unit is being flashed.
            beacon: A port with its beacon lit.
            bootloader: A unit sitting in the bootloader.

        Raises:
            ConfigurationError: If no role is given at all.
        """
        params: dict[str, Any] = {
            role: value
            for role, value in (
                ("inactive", inactive),
                ("active", active),
                ("flashing", flashing),
                ("beacon", beacon),
                ("bootloader", bootloader),
            )
            if value is not None
        }
        if not params:
            raise ConfigurationError("set_colors() needs at least one role")
        await self._post("/api/switch/set_colors", params=params)

    async def reset(self, unit: int, *, bootloader: bool = False) -> None:
        """Reboot a switch unit.

        Args:
            unit: Unit number (``0``-``4``).
            bootloader: Enter reflashing mode after reboot.
        """
        params: dict[str, Any] = {"unit": unit}
        if bootloader:
            params["bootloader"] = 1
        await self._post("/api/switch/reset", params=params)

    async def atx_power(self, port: int | float, action: ATXAction) -> None:
        """ATX power control for a specific port.

        Args:
            port: Port number (``0``-``19`` or unit.port notation).
            action: Power action, one of :data:`ATXAction`.

        Raises:
            APIError: If kvmd does not know the action (HTTP 400).
        """
        await self._post(
            "/api/switch/atx/power", params={"port": port, "action": action}
        )

    async def atx_click(self, port: int | float, button: ATXButton) -> None:
        """Simulate an ATX button click for a specific port.

        Args:
            port: Port number (``0``-``19`` or unit.port notation).
            button: Button to press, one of :data:`ATXButton`.

        Raises:
            APIError: If kvmd does not know the button (HTTP 400).
        """
        await self._post(
            "/api/switch/atx/click", params={"port": port, "button": button}
        )
