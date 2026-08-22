"""RedfishResource tests.

Every payload here is a real kvmd 4.206 response: the documents come from the
capture tool, the empty 204s and the refusals from the hand-recorded
``redfish_actions`` scenario (see ``tests/fixtures/README.md``).
"""

import json
from typing import Any

import httpx
import pytest
import respx

from aiopikvm import APIError, AuthError, PiKVM, ResponseError
from aiopikvm.resources.redfish import RESET_TYPES
from tests.fixtures import DATA_DIR, load_json

RESET_PATH = "/api/redfish/v1/Systems/0/Actions/ComputerSystem.Reset"


def step(name: str) -> dict[str, Any]:
    """Return one recorded Redfish request/response pair.

    Args:
        name: Step name from the ``redfish_actions`` scenario.

    Returns:
        The recorded step.

    Raises:
        KeyError: If the scenario has no such step.
    """
    steps = load_json("redfish_actions")["steps"]
    for recorded in steps:
        if recorded["name"] == name:
            return dict(recorded)
    known = ", ".join(recorded["name"] for recorded in steps)
    raise KeyError(f"Unknown redfish_actions step {name!r}; recorded: {known}")


def replay(name: str) -> httpx.Response:
    """Build the response kvmd was recorded answering *name* with.

    Args:
        name: Step name from the ``redfish_actions`` scenario.

    Returns:
        An httpx response with the recorded status, content type and body.
    """
    recorded = step(name)
    headers = (
        {"Content-Type": recorded["content_type"]} if recorded["content_type"] else {}
    )
    if "text" in recorded:
        return httpx.Response(
            recorded["status"], text=recorded["text"], headers=headers
        )
    if "body_file" in recorded:
        path = DATA_DIR / recorded["body_file"]
        return httpx.Response(
            recorded["status"],
            json=json.loads(path.read_text(encoding="utf-8")),
            headers=headers,
        )
    if recorded["body"] is None:
        return httpx.Response(recorded["status"], headers=headers)
    return httpx.Response(recorded["status"], json=recorded["body"], headers=headers)


def body_of(mock_api: respx.MockRouter) -> Any:
    """Return the JSON body of the last request the router saw.

    Args:
        mock_api: The respx router the client was pointed at.

    Returns:
        The parsed request body.
    """
    return json.loads(mock_api.calls[-1].request.content)


async def test_get_root(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1").mock(
        return_value=httpx.Response(200, json=load_json("redfish_root"))
    )
    root = await client.redfish.get_root()
    assert root["RedfishVersion"] == "1.6.0"
    assert root["Systems"]["@odata.id"] == "/redfish/v1/Systems"


async def test_get_systems(mock_api: respx.MockRouter, client: PiKVM) -> None:
    """The collection is empty with ATX disabled, while Systems/0 resolves."""
    mock_api.get("/api/redfish/v1/Systems").mock(
        return_value=httpx.Response(200, json=load_json("redfish_systems"))
    )
    systems = await client.redfish.get_systems()
    assert systems["Members"] == []
    assert systems["Members@odata.count"] == 0


async def test_get_system(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1/Systems/0").mock(return_value=replay("system_zero"))
    system = await client.redfish.get_system()
    assert system["Id"] == "0"
    assert system["PowerState"] == "Off"


def test_collection_members_are_links_not_ids() -> None:
    """``Members`` holds ``{"@odata.id": ...}`` objects, as the guide says.

    This does not prove it for ``Systems``: that capture is empty — the
    device has ATX disabled and no switch — and kvmd builds the two
    collections in separate files, the Managers one from a hardcoded literal.
    What it pins is the idiom the guide's "take the tail of the path" recipe
    relies on, against the only non-empty Redfish collection captured here.
    """
    members = load_json("redfish_managers")["Members"]
    assert [member["@odata.id"].rsplit("/", 1)[1] for member in members] == ["BMC"]
    assert load_json("redfish_vm")["Members"] == [
        {"@odata.id": "/redfish/v1/Managers/BMC/VirtualMedia/MSD"}
    ]


async def test_get_system_switch_port(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A switch port id is a string, and unreachable while system_id was int.

    Only the URL is asserted: no switch was attached to the capture device,
    so the document served here is the one for ``Systems/0``.
    """
    route = mock_api.get("/api/redfish/v1/Systems/SwitchPort3").mock(
        return_value=httpx.Response(200, json=load_json("redfish_system_0"))
    )
    await client.redfish.get_system("SwitchPort3")
    assert route.called


@pytest.mark.parametrize(
    "name",
    ["system_one", "system_zero_padded", "system_unknown"],
)
async def test_get_system_refused_ids(
    mock_api: respx.MockRouter, client: PiKVM, name: str
) -> None:
    """kvmd compares the id as a string: 1, 00 and bogus are all rejected."""
    recorded = step(name)
    mock_api.get(recorded["path"]).mock(return_value=replay(name))
    with pytest.raises(APIError, match="Missing or invalid Server ID") as info:
        await client.redfish.get_system(recorded["path"].rsplit("/", 1)[1])
    assert info.value.status_code == 400
    assert info.value.error == "HttpError"


async def test_get_system_absent_switch_port(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A well-formed port id on a device with no switch has its own message."""
    mock_api.get("/api/redfish/v1/Systems/SwitchPort0").mock(
        return_value=replay("switch_port_absent")
    )
    with pytest.raises(APIError, match="Non-existent Switch Port ID"):
        await client.redfish.get_system("SwitchPort0")


async def test_get_document_rejects_non_json(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/redfish/v1").mock(
        return_value=httpx.Response(200, text="<html>nope</html>")
    )
    with pytest.raises(ResponseError, match="not JSON"):
        await client.redfish.get_root()


async def test_get_document_rejects_non_object(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/redfish/v1/Systems").mock(
        return_value=httpx.Response(200, json=["0", "SwitchPort1"])
    )
    with pytest.raises(ResponseError, match="JSON list"):
        await client.redfish.get_systems()


async def test_update_system_survives_204(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The PATCH stub answers 204 with no body at all (#44)."""
    mock_api.patch("/api/redfish/v1/Systems/0").mock(
        return_value=replay("patch_system")
    )
    assert await client.redfish.update_system(IndicatorLED="Lit") is None
    assert body_of(mock_api) == {"IndicatorLED": "Lit"}


async def test_update_system_targets_the_given_id(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    route = mock_api.patch("/api/redfish/v1/Systems/SwitchPort2").mock(
        return_value=replay("patch_system")
    )
    await client.redfish.update_system("SwitchPort2", HostName="ignored")
    assert route.called


async def test_reset_survives_204(mock_api: respx.MockRouter, client: PiKVM) -> None:
    """ComputerSystem.Reset answers 204 with no body either (#44)."""
    mock_api.post(RESET_PATH).mock(return_value=replay("reset_accepted"))
    assert await client.redfish.reset("On") is None
    assert body_of(mock_api) == {"ResetType": "On"}


async def test_reset_default_type(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(return_value=replay("reset_accepted"))
    await client.redfish.reset()
    assert body_of(mock_api) == {"ResetType": "ForceRestart"}


async def test_reset_targets_a_switch_port(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The whole point of #57: powering a port of an attached switch."""
    route = mock_api.post(
        "/api/redfish/v1/Systems/SwitchPort1/Actions/ComputerSystem.Reset"
    ).mock(return_value=replay("reset_accepted"))
    await client.redfish.reset("ForceOff", "SwitchPort1")
    assert route.called
    assert body_of(mock_api) == {"ResetType": "ForceOff"}


@pytest.mark.parametrize(
    ("name", "reset_type"),
    [
        ("reset_type_from_the_dmtf_schema", "GracefulRestart"),
        ("reset_type_wrong_case", "forceoff"),
    ],
)
async def test_reset_type_refused(
    mock_api: respx.MockRouter, client: PiKVM, name: str, reset_type: str
) -> None:
    """kvmd matches the six values case-sensitively, and 400s on the rest.

    ``ResetType`` narrows the parameter for a type checker only, and this is
    what that costs nothing at runtime looks like: the request goes out and
    comes back a 400 from the device, exactly as before. CI type-checks
    ``src/`` alone, so the ignore below documents the deliberate violation
    rather than being verified by anything (#68).
    """
    mock_api.post(RESET_PATH).mock(return_value=replay(name))
    with pytest.raises(APIError, match="Missing or invalid ResetType") as info:
        await client.redfish.reset(reset_type)  # type: ignore[arg-type]
    assert info.value.status_code == 400


def test_reset_types_match_the_device() -> None:
    """The constant must stay the list the captured system document allows."""
    system = load_json("redfish_system_0")
    allowed = system["Actions"]["#ComputerSystem.Reset"][
        "ResetType@Redfish.AllowableValues"
    ]
    assert sorted(RESET_TYPES) == sorted(allowed)


def test_set_default_boot_order_is_advertised_but_missing() -> None:
    """The action every system document offers answers a plain-text 404.

    aiopikvm exposes no method for it, so this pins the pair of facts the
    guide warns about rather than any client behaviour.
    """
    actions = load_json("redfish_system_0")["Actions"]
    target = actions["#ComputerSystem.SetDefaultBootOrder"]["target"]
    assert target.endswith("/Actions/ComputerSystem.SetDefaultBootOrder")

    recorded = step("set_default_boot_order")
    assert recorded["path"].endswith(target.replace("/redfish", "/api/redfish"))
    assert recorded["status"] == 404
    assert recorded["content_type"].startswith("text/plain")


async def test_reset_auth_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthError):
        await client.redfish.reset()


async def test_reset_api_error(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post(RESET_PATH).mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    with pytest.raises(APIError):
        await client.redfish.reset()


VM_PATH = "/api/redfish/v1/Managers/BMC/VirtualMedia/MSD"
INSERT_PATH = f"{VM_PATH}/Actions/VirtualMedia.InsertMedia"
EJECT_PATH = f"{VM_PATH}/Actions/VirtualMedia.EjectMedia"


async def test_get_managers(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1/Managers").mock(
        return_value=httpx.Response(200, json=load_json("redfish_managers"))
    )
    managers = await client.redfish.get_managers()
    assert managers["Members@odata.count"] == 1
    assert managers["Members"] == [{"@odata.id": "/redfish/v1/Managers/BMC"}]


async def test_get_manager(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/redfish/v1/Managers/BMC").mock(
        return_value=httpx.Response(200, json=load_json("redfish_manager_bmc"))
    )
    manager = await client.redfish.get_manager()
    assert manager["Id"] == "BMC"
    assert manager["ManagerType"] == "BMC"
    assert manager["VirtualMedia"]["@odata.id"] == (
        "/redfish/v1/Managers/BMC/VirtualMedia"
    )


async def test_get_virtual_media_collection(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/redfish/v1/Managers/BMC/VirtualMedia").mock(
        return_value=httpx.Response(200, json=load_json("redfish_vm"))
    )
    collection = await client.redfish.get_virtual_media_collection()
    assert collection["Members"] == [
        {"@odata.id": "/redfish/v1/Managers/BMC/VirtualMedia/MSD"}
    ]


async def test_get_virtual_media_offline_reports_null_not_false(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """kvmd reads the drive fields only while the MSD is online."""
    mock_api.get(VM_PATH).mock(
        return_value=httpx.Response(200, json=load_json("redfish_vm_msd"))
    )
    media = await client.redfish.get_virtual_media()
    assert media["Id"] == "MSD"
    assert media["Oem"]["PiKVM"]["MsdEnabled"] is True
    assert media["Oem"]["PiKVM"]["MsdOnline"] is False
    # Not False: "not known", because the drive was never read.
    for field in ("Inserted", "WriteProtected", "Image", "ImageName", "ConnectedVia"):
        assert media[field] is None


async def test_insert_media_sends_the_redfish_spelling(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post(INSERT_PATH).mock(return_value=replay("insert_unknown_image"))
    with pytest.raises(APIError):
        await client.redfish.insert_media("no-such-image.iso")
    assert body_of(mock_api) == {
        "Image": "no-such-image.iso",
        "Inserted": True,
        "WriteProtected": True,
    }


async def test_insert_media_passes_both_flags_through(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post(INSERT_PATH).mock(return_value=replay("insert_unknown_image"))
    with pytest.raises(APIError):
        await client.redfish.insert_media(
            "x.img", inserted=False, write_protected=False
        )
    assert body_of(mock_api) == {
        "Image": "x.img",
        "Inserted": False,
        "WriteProtected": False,
    }


async def test_insert_media_on_an_offline_msd_is_a_bare_500(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """kvmd reads state['drive'] before it checks online (redfish/msd.py:137).

    An offline MSD reports `drive` as null, so the attribute lookup raises and
    the handler answers 500 with no error name and no message — nothing that
    says which subsystem was at fault.
    """
    mock_api.post(INSERT_PATH).mock(return_value=replay("insert_unknown_image"))
    with pytest.raises(APIError) as info:
        await client.redfish.insert_media("no-such-image.iso")
    assert info.value.status_code == 500
    assert info.value.error == ""
    assert info.value.error_msg == ""


async def test_insert_media_does_not_take_the_url_the_document_advertises(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The document says Image@Redfish.AllowableValues ['URI']; kvmd reads a
    stored image name.

    This pins the recorded 500, not a refusal: the name validator splits on
    `/` and checks each part as a filename, so the URL passed it and died in
    the offline-MSD crash above, exactly as `no-such-image.iso` does. What an
    online MSD answers a URL is not in the corpus.
    """
    mock_api.post(INSERT_PATH).mock(return_value=replay("insert_a_url"))
    with pytest.raises(APIError) as info:
        await client.redfish.insert_media("https://example.org/ubuntu.iso")
    assert info.value.status_code == 500
    assert info.value.error == ""
    assert info.value.error_msg == ""
    # "Exactly as" is the claim, so it is asserted rather than described: the
    # device answered both the URL and an unknown name with the same thing,
    # which is what "not refused" means here. A 500 on its own is satisfied by
    # any failure at all, and that is all this test used to check (#144).
    for key in ("status", "content_type", "body"):
        assert step("insert_a_url")[key] == step("insert_unknown_image")[key]


async def test_insert_media_without_an_image_is_a_validator_error(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Validation runs before the state read, so this one is a clean 400."""
    mock_api.post(INSERT_PATH).mock(return_value=replay("insert_missing_image"))
    with pytest.raises(APIError) as info:
        await client.redfish.insert_media("")
    assert info.value.status_code == 400
    assert info.value.error == "ValidatorError"


async def test_eject_media_sends_no_body(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post(EJECT_PATH).mock(return_value=replay("eject_offline_drive"))
    with pytest.raises(APIError):
        await client.redfish.eject_media()
    assert mock_api.calls[-1].request.content == b""


async def test_eject_media_on_an_offline_msd_says_which_subsystem(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Unlike InsertMedia, the eject reaches kvmd's MSD plugin and is refused
    properly rather than crashing the handler."""
    mock_api.post(EJECT_PATH).mock(return_value=replay("eject_offline_drive"))
    with pytest.raises(APIError) as info:
        await client.redfish.eject_media()
    assert info.value.status_code == 400
    assert info.value.error == "MsdOfflineError"
