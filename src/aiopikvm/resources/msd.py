"""MSD API — virtual drives and image upload."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from aiopikvm._base_resource import BaseResource
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
        timeout: float | None = None,
    ) -> None:
        """Upload a disk image.

        Args:
            name: Image file name.
            data: Image data as bytes or an async byte iterator.
            timeout: Per-call timeout in seconds. Images are large and the
                client default of 10 s is meant for state calls.
        """
        content = data if isinstance(data, bytes) else _AsyncStream(data)
        await self._post(
            "/api/msd/write",
            params={"image": name},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
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
    def __init__(self, iterator: AsyncIterator[bytes]) -> None:
        self._iterator = iterator

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._iterator:
            yield chunk
