"""Redfish API — DMTF BMC compatibility."""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import APIError


class RedfishResource(BaseResource):
    """Redfish API for DMTF BMC compatibility.

    Redfish does not use the standard PiKVM response format,
    so it calls :pymethod:`PiKVM.request` directly.
    """

    async def reset(self, reset_type: str = "ForceRestart") -> dict[str, Any]:
        """Send a Redfish ComputerSystem.Reset action.

        Args:
            reset_type: The reset type (default ``"ForceRestart"``).

        Returns:
            The JSON response body.
        """
        response = await self._client.request(
            "POST",
            "/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset",
            json={"ResetType": reset_type},
        )
        try:
            result: dict[str, Any] = response.json()
        except (ValueError, TypeError) as exc:
            raise APIError(f"Invalid JSON response: {response.text[:200]}") from exc
        return result
