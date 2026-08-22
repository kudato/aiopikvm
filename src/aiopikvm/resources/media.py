"""Media API — what the kvmd-media daemon can stream."""

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.media import MediaState


class MediaResource(BaseResource):
    """The kvmd-media daemon, which is where live video comes from.

    It runs beside kvmd and serves one REST endpoint and one WebSocket.
    Opening the socket is [`PiKVM.media_ws()`][aiopikvm.PiKVM.media_ws]; this
    resource is the endpoint that says what the socket can be asked for.
    """

    async def get_state(self, *, timeout: float | None = None) -> MediaState:
        """Read what the daemon is configured to stream.

        Args:
            timeout: Per-call timeout in seconds.

        Returns:
            The formats the daemon has, and what it publishes about each.

        Raises:
            APIError: The daemon is not running, which kvmd's nginx reports as
                HTTP 502 — it has no upstream socket to reach.
            ResponseError: The answer carried no ``media`` block.
        """
        result = await self._get("/api/media", timeout=timeout)
        media = result.get("media") if isinstance(result, dict) else None
        return self._validate(MediaState, media, "/api/media")
