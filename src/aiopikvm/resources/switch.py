"""Switch API — multi-port KVM switch and EDID."""

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
