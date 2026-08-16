"""Switch API — multi-port KVM switch and EDID."""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ResponseError
from aiopikvm.models.switch import EDID, SwitchState


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
            port: Port number. Ports are numbered from ``0`` across the whole
                chain; on a multi-unit chain the ``unit.port`` form (``1.3``)
                addresses the same ports. The ``id`` strings in the state
                (``"2.3"``) are display labels and are not accepted here.
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
        """
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
            uplink: Unit whose uplink beacon to control.
            downlink: Unit whose downlink beacon to control.

        Raises:
            ValueError: If not exactly one of *port*, *uplink* or *downlink*
                is given.
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
            raise ValueError(
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

    async def atx_power(self, port: int | float, action: str) -> None:
        """ATX power control for a specific port.

        Args:
            port: Port number (``0``-``19`` or unit.port notation).
            action: Power action (``"on"``, ``"off"``, ``"off_hard"``,
                ``"reset_hard"``).
        """
        await self._post(
            "/api/switch/atx/power", params={"port": port, "action": action}
        )

    async def atx_click(self, port: int | float, button: str) -> None:
        """Simulate an ATX button click for a specific port.

        Args:
            port: Port number (``0``-``19`` or unit.port notation).
            button: Button to simulate (``"power"``, ``"power_long"``,
                ``"reset"``).
        """
        await self._post(
            "/api/switch/atx/click", params={"port": port, "button": button}
        )
