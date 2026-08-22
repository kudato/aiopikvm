"""Redfish API — DMTF BMC compatibility.

kvmd implements enough of the DMTF Redfish schema for generic BMC tooling to
read a machine's power state and change it. It is a subset in two directions:
``ComputerSystem`` exposes power and nothing else, and the documents are plain
Redfish JSON rather than the ``{"ok": ..., "result": ...}`` envelope the rest
of the API uses. Failures still arrive in that envelope, so they reach the
caller as the usual [`APIError`][aiopikvm.APIError] and friends.

The actions answer with HTTP 204 and no body at all, which is why the calls
that perform them return nothing.
"""

from typing import Any, Literal, get_args

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import ResponseError

type ResetType = Literal[
    "On",
    "ForceOn",
    "ForceOff",
    "GracefulShutdown",
    "ForceRestart",
    "PushPowerButton",
]
"""Every ``ResetType`` kvmd accepts, matched case-sensitively.

The DMTF schema defines more — ``GracefulRestart``, ``Nmi``, ``PowerCycle`` —
and kvmd refuses all of them with HTTP 400. Unlike the output, button and
compression names elsewhere in this client, these are not lowercased on the
way in: kvmd looks the name up as given, so ``"forceoff"`` is refused as
surely as a type it has never heard of. Key names are matched the same way.

[`RESET_TYPES`][aiopikvm.resources.redfish.RESET_TYPES] is the same list to
check against at runtime.
"""

RESET_TYPES: tuple[ResetType, ...] = get_args(ResetType.__value__)
"""The values of [`ResetType`][aiopikvm.resources.redfish.ResetType], in a
tuple, for checking at runtime.

Read off the type rather than written out again, so the two cannot drift
apart. The live list is also in each system document under
``Actions["#ComputerSystem.Reset"]["ResetType@Redfish.AllowableValues"]``,
which is what pins this one to a real device.
"""


class RedfishResource(BaseResource):
    """Redfish API for DMTF BMC compatibility.

    Redfish does not use the standard PiKVM response format,
    so it calls [`PiKVM.request()`][aiopikvm.PiKVM.request] directly.
    """

    async def _get_document(self, path: str) -> dict[str, Any]:
        """Send a GET request and parse the Redfish document it returns.

        Args:
            path: URL path.

        Returns:
            The parsed JSON document.

        Raises:
            ResponseError: If the body is not a JSON object.
            PiKVMError: Whatever [`PiKVM.request()`][aiopikvm.PiKVM.request]
                raises — an [`APIError`][aiopikvm.APIError] subclass for the
                status, or a transport failure such as
                [`ConnectError`][aiopikvm.ConnectError].
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
            PiKVMError: Whatever [`PiKVM.request()`][aiopikvm.PiKVM.request]
                raises — an [`APIError`][aiopikvm.APIError] subclass for the
                status, or a transport failure such as
                [`ConnectError`][aiopikvm.ConnectError]. A 2xx is the whole of
                the success signal: kvmd answers 204 with an empty body, and
                any body a later version might add is ignored here rather than
                guessed at.
        """
        await self._client.request(method, path, json=json)

    async def get_root(self) -> dict[str, Any]:
        """Get the Redfish service root.

        Returns:
            Service root document.

        Raises:
            ResponseError: If the body is not a JSON object.
            PiKVMError: If PiKVM refuses the request or is unreachable.
        """
        return await self._get_document("/api/redfish/v1")

    async def get_systems(self) -> dict[str, Any]:
        """Get the systems collection.

        ``Members`` is a list of ``{"@odata.id": "/redfish/v1/Systems/<id>"}``
        links, one for ``"0"`` when the ATX subsystem is enabled and one per
        port of an attached PiKVM Switch. The ids are the tail of those paths,
        not the members themselves. On a device with ATX disabled and no
        switch the collection is empty while ``Systems/0`` still resolves —
        the collection lists what can be powered, not what can be read.

        Returns:
            Systems collection document.

        Raises:
            ResponseError: If the body is not a JSON object.
            PiKVMError: If PiKVM refuses the request or is unreachable.
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
            ``ResetType`` values
            [`reset()`][aiopikvm.resources.redfish.RedfishResource.reset]
            accepts for it.

        Raises:
            ResponseError: If the body is not a JSON object.
            APIError: If the id is not one kvmd knows (HTTP 400), or the
                switch has no such port.
            PiKVMError: If PiKVM is unreachable.
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
            PiKVMError: If PiKVM refuses the request or is unreachable.
        """
        await self._send_action(
            "PATCH", f"/api/redfish/v1/Systems/{system_id}", json=attrs
        )

    async def reset(
        self, reset_type: ResetType = "ForceRestart", system_id: str = "0"
    ) -> None:
        """Send a Redfish ComputerSystem.Reset action.

        This is the Redfish spelling of the ATX calls, and it acts on real
        hardware. Each ``ResetType`` presses one front-panel switch:

        - ``"On"`` and ``"ForceOn"``: a short power click, only if the host
          is off. The two are the same call.
        - ``"ForceOff"``: the power switch held down — 5.5 s by default, and
          configurable — only if the host is on.
        - ``"GracefulShutdown"``: a short power click, only if the host is
          on — the OS decides what to do with it.
        - ``"ForceRestart"``: a click on the *reset* switch, only if the host
          is on. It does not cut the power.
        - ``"PushPowerButton"``: a short power click, with no power-state
          condition. On a switch port it is still dropped while that port is
          busy with an earlier click.

        The default ``"ForceRestart"`` gives the host no chance to shut down
        cleanly. Everything but ``"PushPowerButton"`` is conditional on the
        power state kvmd reads from the host's power LED — the same value it
        reports as ``PowerState``, on both the ``"0"`` and the switch-port
        branch — so a ``"ForceRestart"`` does nothing at all against a host
        kvmd believes to be off, and still answers 204. Where that LED is
        miswired or unread, the conditional types are unpredictable; compare
        ``PowerState`` against reality before relying on them.

        Returns nothing — kvmd answers HTTP 204 with an empty body, and the
        action is asynchronous besides. Read the outcome from
        [`get_system()`][aiopikvm.resources.redfish.RedfishResource.get_system]'s
        ``PowerState``, or from
        [`ATXResource.get_state()`][aiopikvm.resources.atx.ATXResource.get_state].

        With the ATX subsystem disabled in the kvmd config, ``system_id="0"``
        still answers 204 and does nothing at all, so there is no error to
        catch — check ``ATXState.enabled`` first where that matters. A switch
        port is **not** covered by that: kvmd checks ``enabled`` only on the
        ``"0"`` branch, and a ``"SwitchPort<N>"`` reset acts on the port
        whatever the ATX plugin is set to.

        Unlike
        [`get_system()`][aiopikvm.resources.redfish.RedfishResource.get_system],
        this does not bounds-check a switch port: kvmd validates the *form* of
        the id and then drops a command for a port that does not exist, so
        ``"SwitchPort9"`` on a four-port switch answers 204 and does nothing.
        Read the port back with
        [`get_system()`][aiopikvm.resources.redfish.RedfishResource.get_system]
        — that one does answer 400 — if the id came from somewhere you do not
        control.

        Args:
            reset_type: One of
                [`ResetType`][aiopikvm.resources.redfish.ResetType], matched
                case-sensitively. Anything else is refused with HTTP 400
                before any action is taken, ``"GracefulRestart"`` from the
                DMTF schema included.
            system_id: Redfish id of the system to act on: ``"0"`` for the
                machine PiKVM is wired to, or ``"SwitchPort<N>"`` for port
                *N* of an attached PiKVM Switch.

        Raises:
            APIError: If the reset type is not one kvmd accepts, or the id is
                not of a form it knows (HTTP 400). An id whose form is valid
                but whose port does not exist is *not* refused.
            BusyError: If a click on the same line of ``"0"`` is still
                running (HTTP 409) — the power line for every type but
                ``"ForceRestart"``, the reset line for that one; kvmd holds
                the two independently. A busy *switch port* drops the command
                silently instead, and still answers 204.
            PiKVMError: If PiKVM is unreachable.
        """
        await self._send_action(
            "POST",
            f"/api/redfish/v1/Systems/{system_id}/Actions/ComputerSystem.Reset",
            json={"ResetType": reset_type},
        )
