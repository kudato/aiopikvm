"""Streamer API — snapshots, OCR, video stream."""

from typing import Any

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.streamer import OCRInfo, SnapshotImage, StreamerState


class StreamerResource(BaseResource):
    """Streamer management — screenshots and OCR for PiKVM."""

    async def get_state(self) -> StreamerState:
        """Get the current streamer state.

        Returns:
            Current streamer subsystem state.
        """
        return await self._get_model("/api/streamer", StreamerState)

    async def set_params(
        self,
        *,
        quality: int | None = None,
        desired_fps: int | None = None,
        resolution: str | None = None,
        h264_bitrate: int | None = None,
        h264_gop: int | None = None,
        timeout: float | None = None,
    ) -> None:
        """Change the streamer parameters.

        kvmd applies these asynchronously — the call returns once the change
        is queued, and :attr:`StreamerState.applied` is what the running
        streamer ended up with. A parameter the device does not support is
        rejected outright rather than ignored; the ones it supports are the
        keys present in :attr:`StreamerState.params`.

        Args:
            quality: JPEG quality, 1 to 100. Unsupported on devices with no
                adjustable encoder.
            desired_fps: Target frame rate, within
                :attr:`StreamerLimits.desired_fps`.
            resolution: Capture resolution as ``"WIDTHxHEIGHT"``, one of
                :attr:`StreamerLimits.available_resolutions`. Only on
                resolution-capable hardware.
            h264_bitrate: H.264 bitrate in kbps, within
                :attr:`StreamerLimits.h264_bitrate`.
            h264_gop: H.264 group-of-pictures size, within
                :attr:`StreamerLimits.h264_gop`.
            timeout: Per-call timeout in seconds.

        Raises:
            APIError: The device does not support one of the parameters
                (HTTP 400, e.g. ``StreamerH264NotSupported``), or a value is
                out of range.
        """
        params: dict[str, Any] = {
            name: value
            for name, value in (
                ("quality", quality),
                ("desired_fps", desired_fps),
                ("resolution", resolution),
                ("h264_bitrate", h264_bitrate),
                ("h264_gop", h264_gop),
            )
            if value is not None
        }
        await self._post("/api/streamer/set_params", params=params, timeout=timeout)

    async def reset(self, *, timeout: float | None = None) -> None:
        """Restart the streamer process.

        The standard recovery for a pipeline that has frozen or wedged its
        capture device. Video drops for a moment while ustreamer restarts.

        Args:
            timeout: Per-call timeout in seconds.
        """
        await self._post("/api/streamer/reset", timeout=timeout)

    async def snapshot(
        self,
        *,
        allow_offline: bool = False,
        save: bool = False,
        load: bool = False,
        preview: bool = False,
        preview_max_width: int | None = None,
        preview_max_height: int | None = None,
        preview_quality: int | None = None,
        timeout: float | None = None,
    ) -> SnapshotImage:
        """Take a JPEG screenshot.

        Without ``allow_offline``, kvmd returns HTTP 503 whenever the
        video source is not online (host asleep, HDMI unplugged, etc.).
        Passing ``allow_offline=True`` makes kvmd return a "NO LIVE VIDEO"
        placeholder JPEG instead, and the returned
        :attr:`SnapshotImage.online` says which one arrived. The flag has no
        effect when the streamer process is fully stopped (no UI clients) —
        the call still fails with HTTP 503, unless ``load`` is used.

        Args:
            allow_offline: When ``True``, accept a placeholder frame if the
                video source is offline.
            save: Also store this frame as the device's saved snapshot, where
                it shows up in :attr:`StreamerState.snapshot` and survives the
                streamer being stopped.
            load: Return the saved snapshot instead of capturing a new one.
                Works while the streamer is stopped, which is the point.
            preview: Have kvmd scale the image down before sending it.
            preview_max_width: Width bound for the preview. ``0`` or unset
                means a fifth of the source width.
            preview_max_height: Height bound for the preview.
            preview_quality: JPEG quality of the preview, 1 to 100.
            timeout: Per-call timeout in seconds.

        Returns:
            The JPEG together with the metadata ustreamer reports for it.
        """
        params: dict[str, Any] = {}
        if allow_offline:
            params["allow_offline"] = 1
        if save:
            params["save"] = 1
        if load:
            params["load"] = 1
        if preview:
            params["preview"] = 1
        if preview_max_width is not None:
            params["preview_max_width"] = preview_max_width
        if preview_max_height is not None:
            params["preview_max_height"] = preview_max_height
        if preview_quality is not None:
            params["preview_quality"] = preview_quality
        response = await self._get_raw(
            "/api/streamer/snapshot",
            params=params or None,
            accept="image/jpeg",
            timeout=timeout,
        )
        return self._snapshot_image(response)

    async def delete_snapshot(self) -> None:
        """Delete the cached snapshot."""
        await self._delete("/api/streamer/snapshot")

    async def get_ocr_info(self) -> OCRInfo:
        """Get OCR capability metadata (enabled flag, available languages).

        Returns:
            Installed OCR languages and the default selection.
        """
        result = await self._get("/api/streamer/ocr")
        ocr = result.get("ocr") if isinstance(result, dict) else None
        return self._validate(OCRInfo, ocr, "/api/streamer/ocr")

    async def ocr(
        self,
        *,
        langs: list[str] | None = None,
        left: int | None = None,
        top: int | None = None,
        right: int | None = None,
        bottom: int | None = None,
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
            left: Left edge of the region to read, in pixels. Cropping is what
                makes OCR quick: Tesseract needs 10-20 s for a full screen.
            top: Top edge of the region to read.
            right: Right edge of the region to read.
            bottom: Bottom edge of the region to read.
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
        for name, value in (
            ("ocr_left", left),
            ("ocr_top", top),
            ("ocr_right", right),
            ("ocr_bottom", bottom),
        ):
            if value is not None:
                params[name] = value
        if allow_offline:
            params["allow_offline"] = 1
        response = await self._get_raw(
            "/api/streamer/snapshot",
            params=params,
            accept="text/plain",
            timeout=timeout,
        )
        return response.text

    def _snapshot_image(self, response: httpx.Response) -> SnapshotImage:
        """Build a :class:`SnapshotImage` from a snapshot response.

        Args:
            response: The raw snapshot response.

        Returns:
            The image and whatever ustreamer metadata the headers carried.

        Raises:
            ResponseError: If a metadata header is present but unparsable.
        """
        headers = response.headers
        payload: dict[str, Any] = {"data": response.content}
        online = headers.get("X-UStreamer-Online")
        if online is not None:
            payload["online"] = online.strip().lower() == "true"
        for field, header in (
            ("width", "X-UStreamer-Width"),
            ("height", "X-UStreamer-Height"),
            ("timestamp", "X-Timestamp"),
        ):
            value = headers.get(header)
            if value is not None:
                payload[field] = value
        return self._validate(SnapshotImage, payload, "/api/streamer/snapshot")
