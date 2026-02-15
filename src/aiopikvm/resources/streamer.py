"""Streamer API — snapshots, OCR, video stream."""

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.streamer import OCRResult, StreamerState


class StreamerResource(BaseResource):
    """Streamer management — screenshots and OCR for PiKVM."""

    async def get_state(self) -> StreamerState:
        """Get the current streamer state.

        Returns:
            Current streamer subsystem state.
        """
        result = await self._get("/api/streamer")
        return StreamerState.model_validate(result)

    async def snapshot(self) -> bytes:
        """Take a JPEG screenshot.

        Returns:
            Raw JPEG image bytes.
        """
        response = await self._get_raw("/api/streamer/snapshot", accept="image/jpeg")
        return response.content

    async def ocr(self) -> str:
        """Perform OCR on the current screen.

        Returns:
            Recognized text.
        """
        result = await self._get("/api/streamer/ocr")
        return OCRResult.model_validate(result).ocr

    async def delete_snapshot(self) -> None:
        """Delete the cached snapshot."""
        await self._delete("/api/streamer/snapshot")
