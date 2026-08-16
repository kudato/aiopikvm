"""Redfish API — DMTF BMC compatibility.

kvmd implements enough of the DMTF Redfish schema for generic BMC tooling to
read a machine's power state and change it. It is a subset in two directions:
``ComputerSystem`` exposes power and nothing else, and the documents are plain
Redfish JSON rather than the ``{"ok": ..., "result": ...}`` envelope the rest
of the API uses. Failures still arrive in that envelope, so they reach the
caller as the usual :class:`APIError` and friends.

The actions answer with HTTP 204 and no body at all, which is why the calls
that perform them return nothing.
"""

from typing import Any

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ResponseError

RESET_TYPES = (
    "On",
    "ForceOn",
    "ForceOff",
    "GracefulShutdown",
    "ForceRestart",
    "PushPowerButton",
)
"""Every ``ResetType`` kvmd accepts, matched case-sensitively.

The DMTF schema defines more — ``GracefulRestart``, ``Nmi``, ``PowerCycle`` —
and kvmd refuses all of them with HTTP 400. The live list is also in each
system document under
``Actions["#ComputerSystem.Reset"]["ResetType@Redfish.AllowableValues"]``.
"""


class RedfishResource(BaseResource):
    """Redfish API for DMTF BMC compatibility.

    Redfish does not use the standard PiKVM response format,
    so it calls :pymethod:`PiKVM.request` directly.
    """

    async def _get_document(self, path: str) -> dict[str, Any]:
        """Send a GET request and parse the Redfish document it returns.

        Args:
            path: URL path.

        Returns:
            The parsed JSON document.

        Raises:
            ResponseError: If the body is not a JSON object.
            APIError: Whatever :meth:`PiKVM.request` raises for the status.
        """
        response = await self._client.request("GET", path)
        try:
            body = response.json()
        except (ValueError, TypeError) as exc:
            raise ResponseError(
                f"{path} returned a body that is not JSON: {response.text[:200]}",
                response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise ResponseError(
                f"{path} returned a JSON {type(body).__name__} where the "
                "Redfish schema defines an object",
                response.status_code,
            )
        return body

    async def _send_action(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> None:
        """Send a Redfish request that answers with no document.

        Args:
            method: HTTP method.
            path: URL path.
            json: Optional JSON body.

        Raises:
            APIError: Whatever :meth:`PiKVM.request` raises for the status.
                A 2xx is the whole of the success signal: kvmd answers 204
                with an empty body, and any body a later version might add
                is ignored here rather than guessed at.
        """
        await self._client.request(method, path, json=json)

    async def get_root(self) -> dict[str, Any]:
        """Get the Redfish service root.

        Returns:
            Service root document.

        Raises:
            ResponseError: If the body is not a JSON object.
            APIError: If PiKVM refuses the request.
        """
        return await self._get_document("/api/redfish/v1")

    async def get_systems(self) -> dict[str, Any]:
        """Get the systems collection.

        ``Members`` holds ``"0"`` when the ATX subsystem is enabled, plus one
        ``"SwitchPort<N>"`` per port of an attached PiKVM Switch. On a device
        with ATX disabled and no switch the collection is empty while
        ``Systems/0`` still resolves — the collection lists what can be
        powered, not what can be read.

        Returns:
            Systems collection document.

        Raises:
            ResponseError: If the body is not a JSON object.
            APIError: If PiKVM refuses the request.
        """
        return await self._get_document("/api/redfish/v1/Systems")

    async def get_system(self, system_id: str = "0") -> dict[str, Any]:
        """Get details for a specific system.

        Args:
            system_id: Redfish id of the system. kvmd accepts the literal
                ``"0"``, meaning the machine PiKVM itself is wired to, and
                ``"SwitchPort<N>"`` for port *N* of an attached PiKVM Switch.
                Nothing else: ``"1"`` and ``"00"`` are refused with HTTP 400
                like any other unknown id.

        Returns:
            System resource document, including ``PowerState`` and the
            ``ResetType`` values :meth:`reset` accepts for it.

        Raises:
            ResponseError: If the body is not a JSON object.
            APIError: If the id is not one kvmd knows (HTTP 400), or the
                switch has no such port.
        """
        return await self._get_document(f"/api/redfish/v1/Systems/{system_id}")

    async def update_system(self, system_id: str = "0", **attrs: Any) -> None:
        """Send a Redfish system update.

        kvmd accepts this and does nothing: the handler is a stub that
        answers HTTP 204, ignores the body and does not even look at
        *system_id*. It exists so that BMC tooling which PATCHes a system as
        part of its normal flow does not fail (pikvm/pikvm#1525). Nothing
        here changes the device, and a later read returns exactly what it
        returned before.

        Args:
            system_id: Redfish id of the system, ignored by kvmd.
            **attrs: Redfish attributes, ignored by kvmd.

        Raises:
            APIError: If PiKVM refuses the request.
        """
        await self._send_action(
            "PATCH", f"/api/redfish/v1/Systems/{system_id}", json=attrs
        )

    async def reset(
        self, reset_type: str = "ForceRestart", system_id: str = "0"
    ) -> None:
        """Send a Redfish ComputerSystem.Reset action.

        This is the Redfish spelling of the ATX calls, and it acts on real
        hardware: the default ``"ForceRestart"`` cuts the power and brings it
        back, giving the host no chance to shut down cleanly.

        Returns nothing — kvmd answers HTTP 204 with an empty body, and the
        action is asynchronous besides. Read the outcome from
        :meth:`get_system`'s ``PowerState``, or from
        :meth:`ATXResource.get_state`.

        With the ATX subsystem disabled in the kvmd config this still answers
        204 and does nothing at all, so there is no error to catch. Check
        ``ATXState.enabled`` first where that matters.

        Args:
            reset_type: One of :data:`RESET_TYPES`, matched case-sensitively.
                Anything else is refused with HTTP 400 before any action is
                taken, ``"GracefulRestart"`` from the DMTF schema included.
            system_id: Redfish id of the system to act on: ``"0"`` for the
                machine PiKVM is wired to, or ``"SwitchPort<N>"`` for port
                *N* of an attached PiKVM Switch.

        Raises:
            APIError: If the reset type or the id is not one kvmd accepts
                (HTTP 400).
        """
        await self._send_action(
            "POST",
            f"/api/redfish/v1/Systems/{system_id}/Actions/ComputerSystem.Reset",
            json={"ResetType": reset_type},
        )
