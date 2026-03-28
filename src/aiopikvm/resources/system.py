"""System API — device info and logs."""

from typing import Any

from aiopikvm._base_resource import BaseResource


class SystemResource(BaseResource):
    """System information and logs for PiKVM."""

    async def get_info(self, *fields: str) -> dict[str, Any]:
        """Get general device information.

        Args:
            *fields: Optional category filters (``"auth"``, ``"extras"``,
                ``"fan"``, ``"hw"``, ``"meta"``, ``"system"``).
                When omitted, all categories are returned.

        Returns:
            Dictionary with device information grouped by category.
        """
        params: dict[str, Any] | None = None
        if fields:
            params = {"fields": list(fields)}
        result: dict[str, Any] = await self._get("/api/info", params=params)
        return result

    async def get_log(self, *, seek: int = 0) -> str:
        """Get KVMD service logs.

        Args:
            seek: How many seconds of history to return (``0`` = default).

        Returns:
            Log output as plain text.
        """
        params: dict[str, int] = {}
        if seek > 0:
            params["seek"] = seek
        response = await self._get_raw(
            "/api/log", accept="text/plain", params=params if params else None
        )
        return response.text
