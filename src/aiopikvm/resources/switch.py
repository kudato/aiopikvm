"""Switch API — multi-port KVM switch and EDID."""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.switch import EDID, SwitchState


class SwitchResource(BaseResource):
    """Multi-port KVM switch and EDID management for PiKVM."""

    async def get_state(self) -> SwitchState:
        """Get the current switch state.

        Returns:
            Current switch state with active port and port list.
        """
        result = await self._get("/api/switch")
        return SwitchState.model_validate(result)

    async def set_active(self, port: str) -> None:
        """Set the active port.

        Args:
            port: Port identifier to activate.
        """
        await self._post("/api/switch/set_active", params={"port": port})

    async def get_edids(self) -> list[EDID]:
        """Get all EDID profiles.

        Returns:
            List of available EDID profiles.
        """
        result = await self._get("/api/switch/edids")
        return [EDID.model_validate(e) for e in result.get("edids", [])]

    async def create_edid(
        self, edid_id: str, data: str, *, description: str = ""
    ) -> None:
        """Create a new EDID profile.

        Args:
            edid_id: EDID profile identifier.
            data: Raw EDID data as hex string.
            description: Optional human-readable description.
        """
        body: dict[str, str] = {"id": edid_id, "data": data}
        if description:
            body["description"] = description
        await self._post("/api/switch/edids/create", json=body)

    async def change_edid(self, port: str, edid_id: str) -> None:
        """Change the EDID profile for a port.

        Args:
            port: Port identifier.
            edid_id: EDID profile identifier to assign.
        """
        await self._post(
            "/api/switch/edids/change",
            params={"port": port, "edid_id": edid_id},
        )

    async def remove_edid(self, edid_id: str) -> None:
        """Remove an EDID profile.

        Args:
            edid_id: EDID profile identifier to remove.
        """
        await self._post("/api/switch/edids/remove", params={"edid_id": edid_id})

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
        """Control indicator beacon lights.

        Args:
            state: Turn beacon on or off.
            port: Port number (``0``-``19`` or unit.port like ``0.3``).
            uplink: Uplink beacon number.
            downlink: Downlink beacon number.
        """
        params: dict[str, Any] = {"state": int(state)}
        if port is not None:
            params["port"] = port
        if uplink is not None:
            params["uplink"] = uplink
        if downlink is not None:
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

    async def set_colors(self, beacon: str) -> None:
        """Set beacon indicator colors.

        Args:
            beacon: Color in ``RRGGBB:brightness:interval`` hex format
                (e.g. ``"FFA500:BF:0028"``).
        """
        await self._post("/api/switch/set_colors", params={"beacon": beacon})

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
