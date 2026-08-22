"""System API — device info and logs."""

from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm.models.info import InfoState

type InfoField = Literal[
    "auth", "extras", "fan", "health", "hw", "meta", "node", "system", "uptime"
]
"""A category
[`SystemResource.get_info()`][aiopikvm.resources.system.SystemResource.get_info]
may ask for.

Eight of them are kvmd's own submanagers. ``hw`` is not: it is an alias the
legacy shape assembles, and asking for it with ``legacy=False`` is HTTP 400.
"""


class SystemResource(BaseResource):
    """System information and logs for PiKVM."""

    async def get_state(self) -> InfoState:
        """Get device information as a typed state.

        Asks for the per-submanager shape and every category, so the result
        is the whole of ``/api/info`` with none of the legacy rearrangement.
        This is the call the other subsystems' ``get_state()`` is named
        after; [`get_info()`][aiopikvm.resources.system.SystemResource.get_info]
        stays the way to ask for a subset, or for the legacy shape.

        Returns:
            Device information, one attribute per kvmd submanager.

        Raises:
            ResponseError: If the payload does not fit the model.
        """
        return await self._get_model("/api/info", InfoState, params={"legacy": 0})

    async def get_info(self, *fields: InfoField, legacy: bool = True) -> dict[str, Any]:
        """Get general device information.

        kvmd builds this out of eight submanagers — ``auth``, ``extras``,
        ``fan``, ``health``, ``meta``, ``node``, ``system`` and ``uptime`` —
        and then, unless *legacy* is off, rearranges them into the shape its
        older API had:

        - ``hw`` appears, holding ``health`` and the ``platform`` block
          lifted out of ``system``;
        - ``health`` leaves the default set, so a call that names no field
          does not return it;
        - ``system`` loses its ``platform`` whenever ``hw`` is in the same
          request, and is dropped altogether unless it was named too.

        With ``legacy=False`` none of that happens: each submanager comes
        back as it is, ``health`` is in the default set, ``system`` keeps
        its ``platform``, and ``hw`` is refused. That is also the shape the
        WebSocket ``info`` events carry.

        Args:
            *fields: Categories to return. Naming none asks for kvmd's own
                default, which is every category but ``health`` under the
                legacy shape and every category under the modern one.
                ``hw`` is only a field while *legacy* is on.
            legacy: Ask for the legacy shape. ``True`` matches kvmd's own
                default and is what this client has always sent, so the
                request goes out unchanged; ``False`` adds ``legacy=0``.

        Returns:
            Dictionary with device information grouped by category.

        Raises:
            APIError: If a category is not one kvmd knows (HTTP 400) —
                ``hw`` with *legacy* off included, since the modern shape
                has no such submanager.
        """
        params: dict[str, Any] = {}
        if fields:
            # kvmd reads `fields` as a single comma-separated value; passing
            # a list makes httpx send repeated params (fields=a&fields=b) and
            # the server keeps only the first one.
            params["fields"] = ",".join(fields)
        if not legacy:
            params["legacy"] = 0
        result: dict[str, Any] = await self._get("/api/info", params=params or None)
        return result

    async def get_log(self, *, seek: int = 0) -> str:
        """Get KVMD service logs.

        Args:
            seek: How many seconds of history to return (``0`` = default).

        Returns:
            Log output as plain text.
        """
        params: dict[str, int] = {}
        if seek > 0:
            params["seek"] = seek
        response = await self._get_raw(
            "/api/log", accept="text/plain", params=params if params else None
        )
        return response.text

    async def stream_log(
        self,
        *,
        seek: int = 0,
        timeout: float | httpx.Timeout | None = None,
    ) -> AsyncIterator[str]:
        """Stream KVMD service logs in real time.

        Uses ``follow=1`` to keep the connection open and yield new
        log lines as they arrive.

        Args:
            seek: How many seconds of history to return (``0`` = default).
            timeout: Override the request timeout. By default the read
                timeout is disabled — an idle device logs nothing for hours —
                while connect and write keep their client-level values.

        Yields:
            Individual log lines as they arrive.
        """
        params: dict[str, Any] = {"follow": 1}
        if seek > 0:
            params["seek"] = seek
        async with self._stream(
            "GET",
            "/api/log",
            params=params,
            headers={"Accept": "text/plain"},
            timeout=timeout,
        ) as response:
            async for line in response.aiter_lines():
                yield line
