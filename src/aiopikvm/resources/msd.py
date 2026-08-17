"""MSD API — virtual drives and image upload.

The two write endpoints answer differently from everything else in kvmd.
``/api/msd/write`` sends the usual envelope, but with a body worth reading:
it reports the name the image was actually stored under.
``/api/msd/write_remote`` does not send one envelope at all — it streams
``application/x-ndjson``, one envelope per line, and reports a failed
download inside an HTTP 200 rather than as a status.
"""

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError, ResponseError
from aiopikvm.models.msd import MSDState, MSDUpload

_WRITE_PATH = "/api/msd/write"
_WRITE_REMOTE_PATH = "/api/msd/write_remote"

type Compression = Literal["", "none", "lzma", "zstd"]
"""How :meth:`MSDResource.download` may ask kvmd to compress an image.

``""`` and ``"none"`` are the same thing and send the image verbatim;
``"lzma"`` produces what ``.xz`` holds and ``"zstd"`` what ``.zst`` does.
kvmd compresses on the fly on the Pi's own CPU, so the two real modes trade
transfer size against how fast the device can feed the connection.

kvmd lowercases the value before it looks, so only the canonical spelling is
typed. A mode it does not know is HTTP 400.
"""


class MSDResource(BaseResource):
    """Mass Storage Device management for PiKVM."""

    async def get_state(self) -> MSDState:
        """Get the current MSD state.

        Returns:
            Current MSD subsystem state.
        """
        return await self._get_model("/api/msd", MSDState)

    async def set_params(
        self,
        *,
        image: str | None = None,
        cdrom: bool | None = None,
        rw: bool | None = None,
    ) -> None:
        """Set MSD parameters.

        Args:
            image: Name of a stored image to put in the drive, or ``""`` to
                eject the current one. Names come from the storage listing;
                a URL selects a remote image instead. Omit to leave the
                current selection alone.
            cdrom: Emulate CD-ROM drive.
            rw: Allow read-write access.
        """
        params: dict[str, str | int] = {}
        if image is not None:
            params["image"] = image
        if cdrom is not None:
            params["cdrom"] = int(cdrom)
        if rw is not None:
            params["rw"] = int(rw)
        await self._post("/api/msd/set_params", params=params)

    async def set_connected(self, connected: bool) -> None:
        """Set the MSD connection state.

        Args:
            connected: Whether MSD should be connected to the host.
        """
        await self._post("/api/msd/set_connected", params={"connected": int(connected)})

    async def upload(
        self,
        name: str,
        data: bytes | AsyncIterator[bytes],
        *,
        size: int | None = None,
        prefix: str | None = None,
        remove_incomplete: bool | None = None,
        timeout: float | None = None,
    ) -> MSDUpload:
        """Upload a disk image.

        Args:
            name: Image file name. kvmd runs it through its own file-name
                validator and stores it under the result, so read the name
                back from the return value rather than assuming this one.
            data: Image data as bytes or an async byte iterator.
            size: Total image size in bytes. Required for an iterator and
                ignored for bytes. kvmd reads the size from
                ``Content-Length`` and rejects a chunked upload outright, so
                a streamed image has to declare its length up front. It must
                match the data exactly: an undercount makes kvmd store a
                truncated image and mark it ``complete``.
            prefix: Subdirectory of the storage to write into, joined onto
                *name* by kvmd. It has to already exist: kvmd creates the
                image's ``.incomplete`` marker before it creates the
                directory, so a prefix that is not there yet fails on an
                unhandled ``FileNotFoundError`` — a plain-text HTTP 500 with
                no error block, which reaches the caller as an
                :class:`APIError` carrying only the status.
            remove_incomplete: Whether kvmd deletes a partially written image
                if the connection breaks. Leave unset for the kvmd default,
                which is to keep it, listed with ``complete=False``.
            timeout: Per-call timeout in seconds. Images are large and the
                client default of 10 s is meant for state calls.

        Returns:
            What kvmd wrote: the stored ``name``, the ``size`` the write was
            opened for, and how much was ``written``.

        Raises:
            ConfigurationError: If *data* is an iterator and *size* is
                missing, negative, or disagrees with the bytes it yields.
            APIError: If kvmd refuses the write — an image of that name is
                already in storage, the name does not pass its validator, or
                the prefix directory does not exist.
            ResponseError: If the body is not the write info it documents.
            PiKVMError: If PiKVM is unreachable.
        """
        if isinstance(data, bytes):
            length = len(data)
            content: bytes | httpx.AsyncByteStream = data
        else:
            if size is None:
                raise ConfigurationError(
                    "upload() needs the size of a streamed image: kvmd takes "
                    "it from Content-Length and rejects a chunked body"
                )
            if size < 0:
                raise ConfigurationError(f"upload() got a negative size: {size}")
            length = size
            content = _AsyncStream(data, size)
        params: dict[str, Any] = {"image": name}
        if prefix is not None:
            params["prefix"] = prefix
        if remove_incomplete is not None:
            params["remove_incomplete"] = int(remove_incomplete)
        result = await self._post(
            _WRITE_PATH,
            params=params,
            content=content,
            headers={
                "Content-Type": "application/octet-stream",
                # httpx would frame an iterator as Transfer-Encoding: chunked,
                # which leaves kvmd with content_length=None and a 400.
                "Content-Length": str(length),
            },
            timeout=timeout,
        )
        return self._write_info(result, _WRITE_PATH)

    async def upload_remote(
        self,
        url: str,
        *,
        name: str | None = None,
        prefix: str | None = None,
        insecure: bool | None = None,
        remove_incomplete: bool | None = None,
        connect_timeout: float | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> MSDUpload:
        """Download a disk image straight into MSD storage and wait for it.

        The transfer happens between PiKVM and *url*; the image never passes
        through this client. Progress is read from kvmd's own stream, so this
        call lasts as long as the download does — see
        :meth:`upload_remote_progress` to watch it go by.

        Args:
            url: Remote image URL. kvmd's validator accepts ``http`` and
                ``https`` only.
            name: Name to store the image under. kvmd defaults it to the
                remote's own: the ``filename`` of its ``Content-Disposition``
                if it sends a usable one, otherwise the last segment of the
                URL path — and it refuses the whole call if neither is a name
                it will accept.
            prefix: Subdirectory of the storage, with the same
                already-has-to-exist caveat as in :meth:`upload`.
            insecure: Skip TLS verification of the remote — kvmd's own fetch,
                not this client's connection to PiKVM.
            remove_incomplete: Whether kvmd deletes the partial image when
                the download fails. Worth turning on here: a failed remote
                download otherwise leaves an incomplete image occupying the
                name, and the retry is refused for that reason.
            connect_timeout: kvmd's ``timeout`` parameter, in seconds — how
                long *it* waits to connect to *url*. It does not bound the
                download: kvmd puts no limit on the total, and allows a week
                between chunks. Defaults to kvmd's own 10 s; values below 0.1
                are refused.
            timeout: Override this client's timeout for the request. By
                default the read timeout is disabled, since the response
                stays open for the length of the download, while connect and
                write keep their client-level values.

        Returns:
            The last progress record, whose ``name`` is what kvmd stored and
            whose ``written`` equals ``size`` on a completed download.

        Raises:
            APIError: If kvmd refuses before it starts streaming — an
                unusable URL, an origin that answers anything but 200 or
                sends no ``Content-Length``, an unreachable host, or a name
                already in storage — or if the download itself fails, which
                kvmd reports as the last record of an HTTP 200 stream.
            ResponseError: If a record is not the envelope it documents, or
                the stream carries none at all.
            PiKVMError: If PiKVM is unreachable, or the connection breaks
                before kvmd has said why.
        """
        last: MSDUpload | None = None
        async for record in self.upload_remote_progress(
            url,
            name=name,
            prefix=prefix,
            insecure=insecure,
            remove_incomplete=remove_incomplete,
            connect_timeout=connect_timeout,
            timeout=timeout,
        ):
            last = record
        if last is None:
            raise ResponseError(
                f"{_WRITE_REMOTE_PATH} answered without a single progress "
                "record; kvmd sends one before the first byte and one when "
                "the download ends"
            )
        return last

    async def upload_remote_progress(
        self,
        url: str,
        *,
        name: str | None = None,
        prefix: str | None = None,
        insecure: bool | None = None,
        remove_incomplete: bool | None = None,
        connect_timeout: float | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncIterator[MSDUpload]:
        """Download a disk image from a URL, reporting progress as it goes.

        kvmd answers this endpoint with ``application/x-ndjson``: one
        envelope per line, sent before the first byte arrives, about once a
        second while the download runs, and once more when it ends. Each one
        is yielded here as it lands, so ``written / size`` tracks a transfer
        that can take hours.

        Iterating to the end is what waits for the download. Stopping early
        closes the connection, and kvmd gives up on the transfer as soon as
        the next record it writes finds it gone — leaving the partial image
        behind or deleting it, according to *remove_incomplete*. Stop through
        ``contextlib.aclosing`` so that happens where you decide rather than
        whenever the generator is collected.

        A failed download is *not* an error status. kvmd has already sent
        HTTP 200 by then, so it writes the failure as one last record and
        lets the connection break without closing the body properly. This
        raises that record as an :class:`APIError` when it arrives, which is
        before the broken connection surfaces.

        Args:
            url: Remote image URL, ``http`` or ``https``.
            name: Name to store the image under; defaults to the remote's.
            prefix: Subdirectory of the storage, which has to already exist.
            insecure: Skip TLS verification of the remote.
            remove_incomplete: Whether kvmd deletes the partial image when
                the download fails.
            connect_timeout: How long kvmd waits to connect to *url*.
            timeout: Override this client's timeout for the request; the read
                timeout is disabled by default.

        Yields:
            One record per line kvmd sends, in order.

        Raises:
            APIError: If kvmd refuses before streaming, or reports the
                download as failed inside the stream.
            ResponseError: If a line is not the envelope it documents.
            PiKVMError: If PiKVM is unreachable, or the connection breaks
                before kvmd has said why.
        """
        params: dict[str, Any] = {"url": url}
        if name is not None:
            params["image"] = name
        if prefix is not None:
            params["prefix"] = prefix
        if insecure is not None:
            params["insecure"] = int(insecure)
        if remove_incomplete is not None:
            params["remove_incomplete"] = int(remove_incomplete)
        if connect_timeout is not None:
            params["timeout"] = connect_timeout
        async with self._client.stream(
            "POST",
            _WRITE_REMOTE_PATH,
            params=params,
            headers={"Accept": "application/x-ndjson"},
            timeout=(
                timeout
                if timeout is not None
                else httpx.Timeout(self._client._timeout, read=None)
            ),
        ) as response:
            async for line in response.aiter_lines():
                if line.strip():
                    yield self._write_record(line)

    def _write_record(self, line: str) -> MSDUpload:
        """Parse one line of the ``write_remote`` stream.

        Args:
            line: One line of the NDJSON body, without its terminator.

        Returns:
            The progress it carries.

        Raises:
            ResponseError: If the line is not a JSON object, or holds no
                write info.
            APIError: If the record reports the download as failed.
        """
        try:
            body = json.loads(line)
        except ValueError as exc:
            raise ResponseError(
                f"{_WRITE_REMOTE_PATH} sent a line that is not JSON: {line[:200]}"
            ) from exc
        return self._write_info(
            self._unwrap(body, _WRITE_REMOTE_PATH), _WRITE_REMOTE_PATH
        )

    def _write_info(self, result: Any, path: str) -> MSDUpload:
        """Pull the write info out of an unwrapped ``result`` payload.

        Args:
            result: The ``result`` field of a write response envelope.
            path: URL path it came from, for the error message.

        Returns:
            The validated write info.

        Raises:
            ResponseError: If ``result`` holds no ``image`` block, or the
                block does not match :class:`MSDUpload`.
        """
        image = result.get("image") if isinstance(result, dict) else None
        if image is None:
            raise ResponseError(
                f"{path} returned no image block; kvmd answers a write with "
                f'{{"image": {{"name": ..., "size": ..., "written": ...}}}}'
            )
        return self._validate(MSDUpload, image, path)

    async def download(
        self,
        name: str,
        *,
        compress: Compression = "",
        chunk_size: int = 65536,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream a stored image back from the device.

        Args:
            name: Name of the stored image to read.
            compress: Compression kvmd applies on the fly, one of
                :data:`Compression`. The default sends the image verbatim; a
                compressed response carries no ``Content-Length``, so the
                size is unknown until it ends.
            chunk_size: Size of the chunks yielded, in bytes.
            timeout: Override the request timeout. By default the read
                timeout is disabled — an image takes far longer to transfer
                than the client default allows — while connect and write
                keep their client-level values.

        Yields:
            Chunks of the image, in order.

        Raises:
            APIError: If kvmd refuses the read, all of it HTTP 400: no image
                of that name in storage, a compression mode it does not
                know, an MSD that is not set up (``MsdOfflineError``), or a
                drive still handed to the host, which it cannot read from
                underneath (``MsdConnectedError``).
            BusyError: If the MSD is busy with another operation (409).
            PiKVMError: If PiKVM is unreachable, or the connection breaks
                part-way through the image.
        """
        params: dict[str, Any] = {"image": name}
        if compress:
            params["compress"] = compress
        async with self._client.stream(
            "GET",
            "/api/msd/read",
            params=params,
            headers={"Accept": "application/octet-stream"},
            timeout=(
                timeout
                if timeout is not None
                else httpx.Timeout(self._client._timeout, read=None)
            ),
        ) as response:
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk

    async def remove(self, name: str) -> None:
        """Remove a disk image.

        The file is gone when this returns, but the listing kvmd checks a
        write against is rebuilt from the storage a moment later. Uploading
        the same name immediately afterwards is refused as already existing;
        poll :meth:`get_state` until ``storage.images`` has dropped it.

        Args:
            name: Image file name to remove, as it appears in
                ``storage.images`` — including the subdirectory, if it was
                written under one.

        Raises:
            APIError: If no image of that name is in storage, or it is in the
                drive and cannot be removed.
            PiKVMError: If PiKVM is unreachable.
        """
        await self._post("/api/msd/remove", params={"image": name})

    async def reset(self) -> None:
        """Reset the MSD subsystem."""
        await self._post("/api/msd/reset")


class _AsyncStream(httpx.AsyncByteStream):
    """Request body that holds an iterator to the length it promised.

    A ``Content-Length`` that disagrees with the body makes h11 raise
    ``LocalProtocolError``, which is outside the aiopikvm hierarchy and
    escapes ``except PiKVMError``. Worse, an undercount is not an error at
    all on the kvmd side: it reads exactly as many bytes as were announced
    and stores the truncated image as complete. Counting here turns both
    into a ``ConfigurationError`` raised before the mismatch reaches h11.
    """

    def __init__(self, iterator: AsyncIterator[bytes], size: int) -> None:
        self._iterator = iterator
        self._size = size

    async def __aiter__(self) -> AsyncIterator[bytes]:
        sent = 0
        async for chunk in self._iterator:
            sent += len(chunk)
            if sent > self._size:
                raise ConfigurationError(
                    f"upload() was given size={self._size} but the image has "
                    f"more data than that; kvmd would store it truncated"
                )
            yield chunk
        if sent != self._size:
            raise ConfigurationError(
                f"upload() was given size={self._size} but the image ended "
                f"after {sent} bytes"
            )
