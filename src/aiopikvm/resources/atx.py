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
        return await self._get_model("/api/atx", ATXState)

    async def click_power(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Click the power button.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd answers
                only once the click is over, which eats into the client
                default of 10 s.
        """
        await self._post(
            "/api/atx/click",
            params={"button": "power", "wait": int(wait)},
            timeout=timeout,
        )

    async def click_power_long(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Long-press the power button.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd answers
                only once the click is over — the long click alone holds the
                button for 5.5 s of the client default of 10 s.
        """
        await self._post(
            "/api/atx/click",
            params={"button": "power_long", "wait": int(wait)},
            timeout=timeout,
        )

    async def click_reset(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Click the reset button.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd answers
                only once the click is over, which eats into the client
                default of 10 s.
        """
        await self._post(
            "/api/atx/click",
            params={"button": "reset", "wait": int(wait)},
            timeout=timeout,
        )

    async def power_on(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Power on the host.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd holds
                the request until the host reaches the requested state.
        """
        await self._post(
            "/api/atx/power",
            params={"action": "on", "wait": int(wait)},
            timeout=timeout,
        )

    async def power_off(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Power off the host gracefully.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd holds
                the request until the host reaches the requested state.
        """
        await self._post(
            "/api/atx/power",
            params={"action": "off", "wait": int(wait)},
            timeout=timeout,
        )

    async def power_off_hard(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Force power off the host.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd holds
                the request until the host reaches the requested state.
        """
        await self._post(
            "/api/atx/power",
            params={"action": "off_hard", "wait": int(wait)},
            timeout=timeout,
        )

    async def reset_hard(
        self, *, wait: bool = True, timeout: float | None = None
    ) -> None:
        """Force reset the host.

        Args:
            wait: Wait for the operation to complete.
            timeout: Per-call timeout in seconds. With ``wait``, kvmd holds
                the request until the host reaches the requested state.
        """
        await self._post(
            "/api/atx/power",
            params={"action": "reset_hard", "wait": int(wait)},
            timeout=timeout,
        )
