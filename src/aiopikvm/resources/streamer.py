"""Streamer API — snapshots, OCR, video stream."""

import logging
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from typing import Any

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError, ResponseError
from aiopikvm.models.streamer import (
    MJPEGFrame,
    OCRInfo,
    SnapshotImage,
    Streamer,
    StreamerState,
)

_logger = logging.getLogger(__name__)

_DEFAULT_BOUNDARY = b"boundarydonotcross"
"""What ustreamer separates the MJPEG parts with when it names no boundary."""

type _HeaderSpec = tuple[tuple[str, str, Callable[[str], Any]], ...]
"""Which field each ustreamer header fills, and how to read its value."""

_SNAPSHOT_HEADERS: _HeaderSpec = (
    ("online", "X-UStreamer-Online", lambda value: value.strip().lower() == "true"),
    ("width", "X-UStreamer-Width", int),
    ("height", "X-UStreamer-Height", int),
    ("timestamp", "X-Timestamp", float),
)
"""What a snapshot response says about the frame it carries."""

_FRAME_HEADERS: _HeaderSpec = (
    *_SNAPSHOT_HEADERS,
    ("dropped", "X-UStreamer-Dropped", int),
    ("client_fps", "X-UStreamer-Client-FPS", int),
    ("latency", "X-UStreamer-Latency", float),
)
"""The same, plus the per-client counters only the stream parts carry."""


class StreamerResource(BaseResource):
    """Streamer management — screenshots and OCR for PiKVM."""

    async def get_state(self) -> StreamerState:
        """Get the current streamer state.

        Returns:
            Current streamer subsystem state.
        """
        return await self._get_model("/api/streamer", StreamerState)

    async def get_ustreamer_state(self, *, timeout: float | None = None) -> Streamer:
        """Read ustreamer's own state, straight from ustreamer.

        This is the object
        [`StreamerState.streamer`][aiopikvm.StreamerState] holds: kvmd polls
        ``/state`` on the streamer socket and relays the result into
        ``GET /api/streamer`` untouched. Reading it here skips that poll, so
        the numbers are the ones ustreamer has right now rather than the ones
        kvmd last collected — which is what makes
        [`StreamerStream.clients_stat`][aiopikvm.StreamerStream] usable for
        watching a stream this client itself opened.

        Nothing under ``/streamer`` speaks the kvmd envelope on the way out,
        so a failure here has no ``error`` field to match on.

        Args:
            timeout: Per-call timeout in seconds.

        Returns:
            The running streamer's state.

        Raises:
            APIError: The streamer process is not running, which nginx reports
                as HTTP 502 with a page of its own — it has no upstream socket
                to reach. It is not
                [`UnavailableError`][aiopikvm.UnavailableError]: that one means
                HTTP 503, and kvmd never sees this request. kvmd runs the
                streamer while at least one session asks for video, so open a
                [`ws()`][aiopikvm.PiKVM.ws] first and keep reading it.
            ResponseError: The body was not the envelope this endpoint
                documents, which is what a proxy answering instead of
                ustreamer looks like.
        """
        return await self._get_model("/streamer/state", Streamer, timeout=timeout)

    async def mjpeg(
        self,
        *,
        key: str | None = None,
        extra_headers: bool = False,
        zero_data: bool = False,
        chunk_size: int = 65536,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncIterator[MJPEGFrame]:
        """Read the MJPEG stream, one frame at a time.

        This is ustreamer's own ``multipart/x-mixed-replace`` stream, the one
        a browser renders by pointing an ``<img>`` at it. kvmd has no
        equivalent: ``GET /api/streamer/snapshot`` gives one frame per
        request, and this gives them as ustreamer encodes them.

        The iteration ends only when the far end stops sending, so it is a
        loop to be left with a ``break`` or cancelled from outside. The
        streamer has to be running for there to be anything to read, and kvmd
        runs it while at least one session asks for video — so open a
        [`ws()`][aiopikvm.PiKVM.ws] around this and keep reading it, or the
        stream dies under this loop.

        Two of ustreamer's flags are deliberately missing. ``advance_headers``
        sends each part's headers before the frame they describe exists, which
        drops ``Content-Length`` — and every ``X-UStreamer-*`` header with it —
        so no parser that finds frames by their declared length can follow it;
        it is a Chromium rendering workaround with nothing to offer a client
        that reads bytes. ``dual_final_frames`` is the same for Safari.

        Args:
            key: A name for this connection. ustreamer echoes it in
                [`StreamerStream.clients_stat`][aiopikvm.StreamerStream],
                which is the only way to find this reader's own row there —
                the id those are keyed by is assigned by ustreamer and never
                sent to the client it belongs to.
            extra_headers: Ask ustreamer to annotate every part with its
                ``X-UStreamer-*`` headers. Without this only
                [`MJPEGFrame.timestamp`][aiopikvm.MJPEGFrame] is filled in.
            zero_data: Ask for the part headers with no JPEG payload behind
                them, which turns this into a cheap frame-timing feed:
                [`MJPEGFrame.data`][aiopikvm.MJPEGFrame] is then empty.
            chunk_size: How much to read off the socket at a time, in bytes.
            timeout: Override the request timeout. By default the read timeout
                is disabled — a stream has no end to wait for — while connect
                and write keep their client-level values.

        Yields:
            Each frame, with whatever its part headers said about it.

        Raises:
            APIError: The streamer process is not running (HTTP 502 from
                nginx, which has no upstream socket to reach), or the path was
                refused. Nothing under ``/streamer`` carries the kvmd
                envelope, so there is no ``error`` field on either.
            ResponseError: The response was not a multipart stream, or a part
                arrived with no ``Content-Length`` to find its end by.
            PiKVMError: PiKVM became unreachable, or the connection broke
                mid-stream.
        """
        params: dict[str, Any] = {}
        if key is not None:
            params["key"] = key
        if extra_headers:
            params["extra_headers"] = 1
        if zero_data:
            params["zero_data"] = 1
        async with self._client.stream(
            "GET",
            "/streamer/stream",
            params=params or None,
            timeout=(
                timeout
                if timeout is not None
                else httpx.Timeout(self._client._timeout, read=None)
            ),
        ) as response:
            reader = _MultipartReader(_boundary_of(response))
            async for chunk in response.aiter_bytes(chunk_size):
                for headers, data in reader.feed(chunk):
                    payload: dict[str, Any] = {"data": data, "headers": headers}
                    payload.update(_meta_from_headers(headers, _FRAME_HEADERS))
                    yield self._validate(MJPEGFrame, payload, "/streamer/stream")

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
        payload.update(_meta_from_headers(response.headers, _SNAPSHOT_HEADERS))
        return self._validate(SnapshotImage, payload, "/api/streamer/snapshot")


def _meta_from_headers(headers: Mapping[str, str], spec: _HeaderSpec) -> dict[str, Any]:
    """Read ustreamer's annotations off a set of headers.

    A header that is missing or cannot be read is dropped rather than failing
    the call: these are ustreamer's own annotations on a frame the caller
    already has, and only the ones it was asked to send are there at all.

    Args:
        headers: The headers to read, matched without regard to case.
        spec: Which field each header fills, and how to read it.

    Returns:
        The fields that were there and could be parsed.
    """
    lookup = {name.lower(): value for name, value in headers.items()}
    payload: dict[str, Any] = {}
    for field, header, parse in spec:
        value = lookup.get(header.lower())
        if value is None:
            continue
        try:
            payload[field] = parse(value.strip())
        except ValueError:
            _logger.warning("Ignoring unparsable %s header: %r", header, value[:64])
    return payload


def _boundary_of(response: httpx.Response) -> bytes:
    """Find what separates the parts of a multipart response.

    Args:
        response: The streaming response, its body still unread.

    Returns:
        The boundary, without the leading dashes.

    Raises:
        ResponseError: The response is not multipart at all, which is what a
            proxy answering instead of ustreamer looks like.
    """
    content_type = response.headers.get("content-type", "")
    if "multipart/" not in content_type.lower():
        raise ResponseError(
            f"/streamer/stream answered {content_type!r} rather than a "
            "multipart stream; something other than ustreamer replied",
            response.status_code,
        )
    boundary = content_type.partition("boundary=")[2].strip().strip('"')
    return boundary.encode("latin-1") if boundary else _DEFAULT_BOUNDARY


def _part_headers(block: bytes) -> dict[str, str]:
    """Parse one part's header block.

    Args:
        block: The bytes between the boundary line and the blank line, the
            leading newline included.

    Returns:
        The headers, each name in the case it arrived in. A line without a
        colon is skipped, the way an HTTP parser skips one.
    """
    headers: dict[str, str] = {}
    for line in block.decode("latin-1").split("\r\n"):
        name, colon, value = line.partition(":")
        if colon:
            headers[name.strip()] = value.strip()
    return headers


class _MultipartReader:
    """Cut ustreamer's ``multipart/x-mixed-replace`` body into frames.

    Fed the chunks as they arrive, it hands back whole parts and keeps the
    remainder for the next chunk. Parts are found by their declared
    ``Content-Length``: ustreamer sends one on every part it can, and the one
    query flag that makes it stop is the one
    [`StreamerResource.mjpeg()`][aiopikvm.resources.streamer.StreamerResource.mjpeg]
    does not offer.
    """

    __slots__ = ("_buf", "_marker")

    def __init__(self, boundary: bytes) -> None:
        """Prepare a reader.

        Args:
            boundary: The boundary from the response's ``Content-Type``.
        """
        self._marker = b"--" + boundary
        self._buf = b""

    def feed(self, chunk: bytes) -> Iterator[tuple[dict[str, str], bytes]]:
        """Add bytes to the buffer and hand back whatever completed a part.

        Args:
            chunk: The bytes as they came off the socket.

        Yields:
            Each whole part as its headers and its data.

        Raises:
            ResponseError: A part arrived with no ``Content-Length``, so there
                is no way to tell where its data ends.
        """
        self._buf += chunk
        while True:
            part = self._take()
            if part is None:
                return
            yield part

    def _take(self) -> tuple[dict[str, str], bytes] | None:
        """Take the next whole part out of the buffer.

        Returns:
            The part, or ``None`` while the buffer does not hold one yet.

        Raises:
            ResponseError: A part arrived with no ``Content-Length``.
        """
        start = self._buf.find(self._marker)
        if start < 0:
            # Nothing but preamble so far. Keep only enough of it to
            # recognise a boundary split across two chunks.
            self._buf = self._buf[-len(self._marker) :]
            return None
        if start:
            self._buf = self._buf[start:]
        if self._buf[len(self._marker) : len(self._marker) + 2] == b"--":
            # The closing boundary. ustreamer's stream has no end, so this
            # only turns up when something else finished the body for it.
            self._buf = b""
            return None
        head_end = self._buf.find(b"\r\n\r\n", len(self._marker))
        if head_end < 0:
            return None
        headers = _part_headers(self._buf[len(self._marker) : head_end])
        raw_length = headers.get("Content-Length")
        if raw_length is None:
            raise ResponseError(
                "A frame of the MJPEG stream arrived with no Content-Length, "
                "so there is no way to tell where it ends. ustreamer drops "
                "that header under advance_headers, which mjpeg() does not "
                "ask for — something between the client and the device is "
                "rewriting the stream"
            )
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ResponseError(
                f"A frame of the MJPEG stream declared Content-Length "
                f"{raw_length!r}, which is not a length"
            ) from exc
        body_at = head_end + 4
        if len(self._buf) < body_at + length:
            return None
        data = self._buf[body_at : body_at + length]
        self._buf = self._buf[body_at + length :]
        return (headers, data)
