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

    async def snapshot(self, *, allow_offline: bool = False) -> bytes:
        """Take a JPEG screenshot.

        Without ``allow_offline``, kvmd returns HTTP 503 whenever the
        video source is not online (host asleep, HDMI unplugged, etc.).
        Passing ``allow_offline=True`` makes kvmd return a "NO LIVE VIDEO"
        placeholder JPEG instead. The flag has no effect when the streamer
        process is fully stopped (no UI clients) — the call still fails
        with HTTP 503.

        Args:
            allow_offline: When ``True``, accept a placeholder frame if the
                video source is offline.

        Returns:
            Raw JPEG image bytes.
        """
        params: dict[str, Any] = {}
        if allow_offline:
            params["allow_offline"] = 1
        response = await self._get_raw(
            "/api/streamer/snapshot",
            params=params or None,
            accept="image/jpeg",
        )
        return response.content

    async def get_ocr_info(self) -> OCRInfo:
        """Get OCR capability metadata (enabled flag, available languages).

        Returns:
            Installed OCR languages and the default selection.
        """
        result = await self._get("/api/streamer/ocr")
        return OCRInfo.model_validate(result["ocr"])

    async def ocr(
        self,
        *,
        langs: list[str] | None = None,
        allow_offline: bool = False,
        timeout: float = 30.0,
    ) -> str:
        """Perform OCR on the current screen.

        Sends ``GET /api/streamer/snapshot?ocr=1`` — the kvmd snapshot
        endpoint with the ``ocr`` flag, which returns recognized text as
        ``text/plain`` instead of a JPEG.

        Args:
            langs: Tesseract language codes (e.g. ``["eng"]``,
                ``["eng", "rus"]``). When omitted the kvmd default is used.
                Available languages can be queried via :meth:`get_ocr_info`.
            allow_offline: When ``True``, run OCR on the "NO LIVE VIDEO"
                placeholder if the video source is offline; otherwise
                kvmd returns HTTP 503. No effect if the streamer process
                is fully stopped.
            timeout: Per-call timeout in seconds. OCR runs Tesseract on the
                Pi CPU and is intrinsically slow (10-20 s for full-screen),
                so the default is wider than the client-level default.

        Returns:
            Recognized text.
        """
        params: dict[str, Any] = {"ocr": 1}
        if langs:
            params["ocr_langs"] = ",".join(langs)
        if allow_offline:
            params["allow_offline"] = 1
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
