"""MSD API — virtual drives and image upload."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ConfigurationError
from aiopikvm.models.msd import MSDState


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
    ) -> None:
        """Upload a disk image.

        Args:
            name: Image file name.
            data: Image data as bytes or an async byte iterator.
            size: Total image size in bytes. Required for an iterator and
                ignored for bytes. kvmd reads the size from
                ``Content-Length`` and rejects a chunked upload outright, so
                a streamed image has to declare its length up front. It must
                match the data exactly: an undercount makes kvmd store a
                truncated image and mark it ``complete``.
            prefix: Subdirectory of the storage to write into.
            remove_incomplete: Whether kvmd deletes a partially written image
                if the connection breaks. Leave unset for the kvmd default.
            timeout: Per-call timeout in seconds. Images are large and the
                client default of 10 s is meant for state calls.

        Raises:
            ConfigurationError: If *data* is an iterator and *size* is
                missing, negative, or disagrees with the bytes it yields.
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
        await self._post(
            "/api/msd/write",
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

    async def upload_remote(self, url: str, *, timeout: float = 0) -> None:
        """Upload a disk image from a remote URL.

        Args:
            url: Remote image URL.
            timeout: Download timeout in seconds (``0`` = server default).
                Unlike the ``timeout`` of the other calls, this one is a
                query parameter kvmd applies to its own download; it does
                not bound how long this client waits.
        """
        params: dict[str, Any] = {"url": url}
        if timeout > 0:
            params["timeout"] = timeout
        await self._post("/api/msd/write_remote", params=params)

    async def download(
        self,
        name: str,
        *,
        compress: str = "",
        chunk_size: int = 65536,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream a stored image back from the device.

        Args:
            name: Name of the stored image to read.
            compress: Compression kvmd applies on the fly — ``"lzma"`` or
                ``"zstd"``. Empty (the default) and ``"none"`` both send the
                image verbatim; a compressed response carries no
                ``Content-Length``, so the size is unknown until it ends.
            chunk_size: Size of the chunks yielded, in bytes.
            timeout: Override the request timeout. By default the read
                timeout is disabled — an image takes far longer to transfer
                than the client default allows — while connect and write
                keep their client-level values.

        Yields:
            Chunks of the image, in order.
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

        Args:
            name: Image file name to remove.
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
