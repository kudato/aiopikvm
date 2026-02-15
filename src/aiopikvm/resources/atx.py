"""ATX API — host power control."""

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.atx import ATXState


class ATXResource(BaseResource):
    """ATX power management for the host machine."""

    async def get_state(self) -> ATXState:
        """Get the current ATX state.

        Returns:
            Current ATX subsystem state including LED indicators.
        """
        result = await self._get("/api/atx")
        return ATXState.model_validate(result)

    async def click_power(self, *, wait: bool = True) -> None:
        """Click the power button.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post(
            "/api/atx/click", params={"button": "power", "wait": int(wait)}
        )

    async def click_power_long(self, *, wait: bool = True) -> None:
        """Long-press the power button.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post(
            "/api/atx/click", params={"button": "power_long", "wait": int(wait)}
        )

    async def click_reset(self, *, wait: bool = True) -> None:
        """Click the reset button.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post(
            "/api/atx/click", params={"button": "reset", "wait": int(wait)}
        )

    async def power_on(self, *, wait: bool = True) -> None:
        """Power on the host.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post("/api/atx/power", params={"action": "on", "wait": int(wait)})

    async def power_off(self, *, wait: bool = True) -> None:
        """Power off the host gracefully.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post("/api/atx/power", params={"action": "off", "wait": int(wait)})

    async def power_off_hard(self, *, wait: bool = True) -> None:
        """Force power off the host.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post(
            "/api/atx/power", params={"action": "off_hard", "wait": int(wait)}
        )

    async def reset_hard(self, *, wait: bool = True) -> None:
        """Force reset the host.

        Args:
            wait: Wait for the operation to complete.
        """
        await self._post(
            "/api/atx/power", params={"action": "reset_hard", "wait": int(wait)}
        )
