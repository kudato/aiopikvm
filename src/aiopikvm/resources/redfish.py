"""Redfish API — DMTF BMC compatibility."""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import APIError


class RedfishResource(BaseResource):
    """Redfish API for DMTF BMC compatibility.

    Redfish does not use the standard PiKVM response format,
    so it calls :pymethod:`PiKVM.request` directly.
    """

    async def _redfish_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a Redfish request and parse the JSON response.

        Args:
            method: HTTP method.
            path: URL path.
            json: Optional JSON body.

        Returns:
            Parsed JSON response.
        """
        response = await self._client.request(method, path, json=json)
        try:
            result: dict[str, Any] = response.json()
        except (ValueError, TypeError) as exc:
            raise APIError(f"Invalid JSON response: {response.text[:200]}") from exc
        return result

    async def get_root(self) -> dict[str, Any]:
        """Get the Redfish service root.

        Returns:
            Service root document.
        """
        return await self._redfish_request("GET", "/api/redfish/v1")

    async def get_systems(self) -> dict[str, Any]:
        """Get the systems collection.

        Returns:
            Systems collection document.
        """
        return await self._redfish_request("GET", "/api/redfish/v1/Systems")

    async def get_system(self, system_id: int = 0) -> dict[str, Any]:
        """Get details for a specific system.

        Args:
            system_id: System index (default ``0``).

        Returns:
            System resource document.
        """
        return await self._redfish_request(
            "GET", f"/api/redfish/v1/Systems/{system_id}"
        )

    async def update_system(self, system_id: int = 0, **attrs: Any) -> dict[str, Any]:
        """Update system configuration.

        Args:
            system_id: System index (default ``0``).
            **attrs: Redfish attributes to update
                (e.g. ``IndicatorLED="Lit"``).

        Returns:
            Updated system resource document.
        """
        return await self._redfish_request(
            "PATCH", f"/api/redfish/v1/Systems/{system_id}", json=attrs
        )

    async def reset(self, reset_type: str = "ForceRestart") -> dict[str, Any]:
        """Send a Redfish ComputerSystem.Reset action.

        Args:
            reset_type: The reset type (default ``"ForceRestart"``).

        Returns:
            The JSON response body.
        """
        return await self._redfish_request(
            "POST",
            "/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset",
            json={"ResetType": reset_type},
        )
