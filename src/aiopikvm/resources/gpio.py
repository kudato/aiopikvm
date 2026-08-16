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
            wait: Answer only once the switch has finished. Without it kvmd
                replies as soon as the action starts and writes anything that
                goes wrong after that — an offline driver, most of all — to
                its own log only. A busy channel is reported either way.
                Defaults to ``False``, matching kvmd; note that
                :meth:`ATXResource` calls default to waiting instead.
            timeout: Per-call timeout in seconds. Only meaningful with
                ``wait``, which holds the request open for the duration of
                the switch.

        Raises:
            BusyError: The channel is already running another action (409).
                Raised with or without ``wait``.
            APIError: No such channel, or the channel is not switchable (400).
                With ``wait`` this also covers a driver that went offline
                mid-action, which is otherwise only logged by kvmd.
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
            delay: Pulse duration in seconds. ``None`` and ``0`` both mean the
                channel default; kvmd clamps anything else to the channel's
                ``min_delay``/``max_delay``.
            wait: Answer only once the pulse has finished. Without it kvmd
                replies as soon as the pulse starts and writes anything that
                goes wrong after that — an offline driver, most of all — to
                its own log only. A busy channel is reported either way.
            timeout: Per-call timeout in seconds. A pulse with ``wait`` holds
                the request open for its whole duration, which can outlast
                the client default.

        Raises:
            BusyError: The channel is already running another action (409).
                Raised with or without ``wait``.
            APIError: No such channel, or the channel does not support pulsing
                because its ``pulse.delay`` is ``0`` (400). With ``wait`` this
                also covers a driver that went offline mid-pulse, which is
                otherwise only logged by kvmd.
        """
        params: dict[str, str | float | int] = {"channel": channel}
        if delay is not None:
            params["delay"] = delay
        if wait:
            params["wait"] = 1
        await self._post("/api/gpio/pulse", params=params, timeout=timeout)
