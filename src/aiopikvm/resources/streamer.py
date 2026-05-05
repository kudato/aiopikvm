"""Streamer API — snapshots, OCR, video stream."""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.streamer import OCRInfo, StreamerState


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

    async def get_ocr_info(self) -> OCRInfo:
        """Get OCR capability metadata (enabled flag, available languages).

        Returns:
            Installed OCR languages and the default selection.
        """
        result = await self._get("/api/streamer/ocr")
        return OCRInfo.model_validate(result["ocr"])

    async def ocr(
        self, *, langs: list[str] | None = None, timeout: float = 30.0
    ) -> str:
        """Perform OCR on the current screen.

        Sends ``GET /api/streamer/snapshot?ocr=1`` — the kvmd snapshot
        endpoint with the ``ocr`` flag, which returns recognized text as
        ``text/plain`` instead of a JPEG.

        Args:
            langs: Tesseract language codes (e.g. ``["eng"]``,
                ``["eng", "rus"]``). When omitted the kvmd default is used.
                Available languages can be queried via :meth:`get_ocr_info`.
            timeout: Per-call timeout in seconds. OCR runs Tesseract on the
                Pi CPU and is intrinsically slow (10-20 s for full-screen),
                so the default is wider than the client-level default.

        Returns:
            Recognized text.
        """
        params: dict[str, Any] = {"ocr": 1}
        if langs:
            params["ocr_langs"] = ",".join(langs)
        response = await self._get_raw(
            "/api/streamer/snapshot",
            params=params,
            accept="text/plain",
            timeout=timeout,
        )
        return response.text

    async def delete_snapshot(self) -> None:
        """Delete the cached snapshot."""
        await self._delete("/api/streamer/snapshot")
