"""Streamer API — snapshots, OCR, video stream."""

import logging
from typing import Any

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError
from aiopikvm.models.streamer import OCRInfo, SnapshotImage, StreamerState

_logger = logging.getLogger(__name__)


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
        is queued, and [`StreamerState.applied`][aiopikvm.StreamerState] is
        what the running streamer ended up with. Read it back to confirm: a
        value outside the device's own limits is accepted with HTTP 200 and
        then dropped silently, so only re-reading the state shows what
        happened. What is rejected outright is a parameter the device does not
        have at all — the ones it has are the keys present in
        [`StreamerState.params`][aiopikvm.StreamerState].

        Asynchronously here means about a second: kvmd holds the batch open
        for further writes, then applies it and **restarts the streamer**, so
        video drops for a moment. Until that happens neither ``params`` nor
        ``applied`` moves — both describe the streamer that is still running.

        That lag has a sharp edge. kvmd compares each incoming value against
        the *running* streamer and queues only what differs, so writing the
        old value back does not cancel a pending change: it is equal to what
        is running, so it is dropped, and the pending change lands a moment
        later. Undoing a write means waiting for it to take and then writing
        the old value — by which time it differs again.

        Args:
            quality: JPEG quality, 1 to 100. Unsupported on devices with no
                adjustable encoder.
            desired_fps: Target frame rate, 0 to 120 for kvmd, and within
                [`StreamerLimits.desired_fps`][aiopikvm.StreamerLimits] to
                actually take effect.
            resolution: Capture resolution as ``"WIDTHxHEIGHT"``, one of
                [`StreamerLimits.available_resolutions`][aiopikvm.StreamerLimits].
                Only on resolution-capable hardware.
            h264_bitrate: H.264 bitrate in kbps, 25 to 20000 for kvmd, and
                within
                [`StreamerLimits.h264_bitrate`][aiopikvm.StreamerLimits] to
                take effect.
            h264_gop: H.264 group-of-pictures size, 0 to 60 for kvmd, and
                within [`StreamerLimits.h264_gop`][aiopikvm.StreamerLimits] to
                take effect.
            timeout: Per-call timeout in seconds.

        Raises:
            ConfigurationError: If no parameter is given at all.
            APIError: The device does not have one of these parameters
                (HTTP 400, e.g. ``StreamerH264NotSupported``), or a value is
                outside the range kvmd validates against.
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
        if not params:
            raise ConfigurationError("set_params() needs at least one parameter")
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

        Without ``allow_offline``, kvmd returns HTTP 503 whenever the video
        source is not online (host asleep, HDMI unplugged, etc.). Passing
        ``allow_offline=True`` makes kvmd return a "NO LIVE VIDEO" placeholder
        JPEG instead, and the returned
        [`SnapshotImage.online`][aiopikvm.SnapshotImage] says which one
        arrived. The flag has no effect when the streamer process is fully
        stopped (no UI clients) — the call still fails with HTTP 503, unless
        ``load`` is used.

        Args:
            allow_offline: When ``True``, accept a placeholder frame if the
                video source is offline.
            save: Also store this frame as the device's saved snapshot, where
                it shows up in
                [`StreamerState.snapshot`][aiopikvm.StreamerState] and
                survives the streamer being stopped. Ignored together with
                ``load``, which returns before anything is saved.
            load: Return the saved snapshot instead of capturing a new one.
                Works while the streamer is stopped, which is the point.
            preview: Have kvmd scale the image down before sending it. The
                reported ``width`` and ``height`` still describe the source
                frame, not the scaled data.
            preview_max_width: Width bound for the preview. Leaving *both*
                bounds unset gives a fifth of the source size; setting only
                this one leaves the height at the source height.
            preview_max_height: Height bound for the preview.
            preview_quality: JPEG quality of the preview, 1 to 100.
            timeout: Per-call timeout in seconds.

        Returns:
            The JPEG together with the metadata ustreamer reports for it.

        Raises:
            UnavailableError: The video source is offline and
                ``allow_offline`` was not set, the streamer process is
                stopped, or ``load`` was used with nothing saved (HTTP 503).
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
                Available languages can be queried via
                [`get_ocr_info()`][aiopikvm.resources.streamer.StreamerResource.get_ocr_info].
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
        """Build a [`SnapshotImage`][aiopikvm.SnapshotImage] from a snapshot
        response.

        A header that cannot be read is dropped rather than failing the call:
        the JPEG is what the caller asked for, and these are ustreamer's own
        annotations, which no capture in this repository pins down.

        Args:
            response: The raw snapshot response.

        Returns:
            The image and whatever ustreamer metadata the headers carried.
        """
        payload: dict[str, Any] = {"data": response.content}
        online = response.headers.get("X-UStreamer-Online")
        if online is not None:
            payload["online"] = online.strip().lower() == "true"
        for field, header, parse in (
            ("width", "X-UStreamer-Width", int),
            ("height", "X-UStreamer-Height", int),
            ("timestamp", "X-Timestamp", float),
        ):
            value = response.headers.get(header)
            if value is None:
                continue
            try:
                payload[field] = parse(value.strip())
            except ValueError:
                _logger.warning("Ignoring unparsable %s header: %r", header, value[:64])
        return self._validate(SnapshotImage, payload, "/api/streamer/snapshot")
