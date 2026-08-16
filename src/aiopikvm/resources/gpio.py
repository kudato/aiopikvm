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
        return await self._get_model("/api/gpio", GPIOState)

    async def switch(
        self,
        channel: str,
        state: bool,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Set a GPIO output channel state.

        Args:
            channel: Channel name.
            state: Desired state (``True`` = on, ``False`` = off).
            wait: Answer only once the switch has happened, so that a failure
                is reported as an error instead of being logged server-side
                and silently dropped. kvmd returns HTTP 409 if the channel is
                already busy with another action.
            timeout: Per-call timeout in seconds. Only meaningful with
                ``wait``, which holds the request open for the duration of
                the switch.
        """
        params: dict[str, str | int] = {"channel": channel, "state": int(state)}
        if wait:
            params["wait"] = 1
        await self._post("/api/gpio/switch", params=params, timeout=timeout)

    async def pulse(
        self,
        channel: str,
        delay: float | None = None,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> None:
        """Send a pulse to a GPIO channel.

        Args:
            channel: Channel name.
            delay: Pulse duration in seconds (``None`` = server default).
                kvmd clamps it to the channel's ``min_delay``/``max_delay``.
            wait: Answer only once the pulse has finished, so that a failure
                is reported as an error instead of being logged server-side
                and silently dropped. kvmd returns HTTP 409 if the channel is
                already busy with another action.
            timeout: Per-call timeout in seconds. A pulse with ``wait`` holds
                the request open for its whole duration, which can outlast
                the client default.
        """
        params: dict[str, str | float | int] = {"channel": channel}
        if delay is not None:
            params["delay"] = delay
        if wait:
            params["wait"] = 1
        await self._post("/api/gpio/pulse", params=params, timeout=timeout)
