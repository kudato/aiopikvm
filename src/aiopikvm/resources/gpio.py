"""GPIO API — GPIO channel control."""

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.gpio import GPIOState


class GPIOResource(BaseResource):
    """GPIO channel management for PiKVM."""

    async def get_state(self) -> GPIOState:
        """Get the current GPIO state.

        Returns:
            Current GPIO subsystem state with inputs and outputs.
        """
        result = await self._get("/api/gpio")
        return GPIOState.model_validate(result)

    async def switch(self, channel: str, state: bool) -> None:
        """Set a GPIO output channel state.

        Args:
            channel: Channel name.
            state: Desired state (``True`` = on, ``False`` = off).
        """
        await self._post(
            "/api/gpio/switch",
            params={"channel": channel, "state": int(state)},
        )

    async def pulse(self, channel: str, delay: float | None = None) -> None:
        """Send a pulse to a GPIO channel.

        Args:
            channel: Channel name.
            delay: Pulse duration in seconds (``None`` = server default).
        """
        params: dict[str, str | float] = {"channel": channel}
        if delay is not None:
            params["delay"] = delay
        await self._post("/api/gpio/pulse", params=params)
