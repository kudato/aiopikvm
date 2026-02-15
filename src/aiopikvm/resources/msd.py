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
        result = await self._get("/api/msd")
        return MSDState.model_validate(result)

    async def set_params(
        self,
        *,
        cdrom: bool | None = None,
        rw: bool | None = None,
    ) -> None:
        """Set MSD parameters.

        Args:
            cdrom: Emulate CD-ROM drive.
            rw: Allow read-write access.
        """
        params: dict[str, int] = {}
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
    ) -> None:
        """Upload a disk image.

        Args:
            name: Image file name.
            data: Image data as bytes or an async byte iterator.
        """
        content = data if isinstance(data, bytes) else _AsyncStream(data)
        await self._post(
            "/api/msd/write",
            params={"image": name},
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

    async def upload_remote(self, url: str, *, timeout: float = 0) -> None:
        """Upload a disk image from a remote URL.

        Args:
            url: Remote image URL.
            timeout: Download timeout in seconds (``0`` = server default).
        """
        params: dict[str, Any] = {"url": url}
        if timeout > 0:
            params["timeout"] = timeout
        await self._post("/api/msd/write_remote", params=params)

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
