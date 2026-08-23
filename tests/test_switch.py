"""SwitchResource tests."""

from typing import get_type_hints

import httpx
import pytest
import respx

from aiopikvm import ConfigurationError, PiKVM, ResponseError, SwitchColor
from aiopikvm.resources.switch import SwitchResource
from tests.fixtures import load_json

ROLES = ("inactive", "active", "flashing", "beacon", "bootloader")

OK = {"ok": True, "result": {}}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/switch").mock(
        return_value=httpx.Response(200, json=load_json("switch"))
    )
    state = await client.switch.get_state()
    # No switch is attached to the capture device, so every per-port list is
    # empty and nothing is selected.
    assert state.summary.active_port == -1
    assert state.summary.active_id == ""
    assert state.summary.synced is True
    assert state.model.ports == []
    assert state.model.units == []
    assert state.video.links == []
    assert state.atx.busy == []


async def test_get_state_reads_the_limits(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/switch").mock(
        return_value=httpx.Response(200, json=load_json("switch"))
    )
    state = await client.switch.get_state()
    limits = state.model.limits.atx.click_delays
    assert limits.power.max == 10
    assert limits.power_long.default > limits.power.default
    assert state.model.firmware.version > 0


async def test_get_state_reads_the_colors(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/switch").mock(
        return_value=httpx.Response(200, json=load_json("switch"))
    )
    state = await client.switch.get_state()
    assert state.colors.active.green == 255
    # Only the beacon role blinks by default.
    assert state.colors.beacon.blink_ms == 250
    assert state.colors.inactive.blink_ms == 0


async def test_get_state_reads_the_edids(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/switch").mock(
        return_value=httpx.Response(200, json=load_json("switch"))
    )
    state = await client.switch.get_state()
    edid = state.edids.all["default"]
    assert edid.name == "Default"
    assert edid.parsed is not None
    # The monitor identity is replaced with placeholders in the fixture; see
    # scrub_edid() in the capture tool.
    assert edid.parsed.mfc_id == "AAA"
    assert edid.parsed.monitor_name == "DUMMY SCREEN"
    assert state.edids.used == []


async def test_get_edids(mock_api: respx.MockRouter, client: PiKVM) -> None:
    # There is no GET /switch/edids endpoint; the catalogue lives in the state.
    mock_api.get("/api/switch").mock(
        return_value=httpx.Response(200, json=load_json("switch"))
    )
    edids = await client.switch.get_edids()
    assert "default" in edids
    assert edids["default"].data


async def test_set_active(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_active(1)
    assert mock_api.calls[-1].request.url.params["port"] == "1"


async def test_set_active_unit_port(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_active(1.3)
    assert mock_api.calls[-1].request.url.params["port"] == "1.3"


def _edid_hex() -> str:
    """A blob of the length kvmd accepts, taken from the capture."""
    data: str = load_json("switch")["result"]["edids"]["all"]["default"]["data"]
    return data


async def test_create_edid(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/edids/create").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"id": "9b3c1e0a"}}
        )
    )
    blob = _edid_hex()
    edid_id = await client.switch.create_edid("Monitor", blob)
    assert edid_id == "9b3c1e0a"
    params = mock_api.calls[-1].request.url.params
    assert params["name"] == "Monitor"
    assert params["data"] == blob


async def test_create_edid_without_id(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/switch/edids/create").mock(
        return_value=httpx.Response(200, json=OK)
    )
    with pytest.raises(ResponseError, match="did not return the new EDID id"):
        await client.switch.create_edid("Monitor", _edid_hex())


async def test_change_edid(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/edids/change").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.change_edid("9b3c1e0a", name="Renamed")
    params = mock_api.calls[-1].request.url.params
    assert params["id"] == "9b3c1e0a"
    assert params["name"] == "Renamed"
    assert "data" not in params


async def test_remove_edid(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/edids/remove").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.remove_edid("9b3c1e0a")
    assert mock_api.calls[-1].request.url.params["id"] == "9b3c1e0a"


async def test_set_active_prev(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active_prev").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_active_prev()


async def test_set_active_next(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_active_next").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_active_next()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"port": 3}, ("port", "3")),
        ({"uplink": 1}, ("uplink", "1")),
        ({"downlink": 0}, ("downlink", "0")),
    ],
)
async def test_set_beacon(
    mock_api: respx.MockRouter,
    client: PiKVM,
    kwargs: dict[str, int],
    expected: tuple[str, str],
) -> None:
    mock_api.post("/api/switch/set_beacon").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_beacon(True, **kwargs)
    params = mock_api.calls[-1].request.url.params
    assert params["state"] == "1"
    assert params[expected[0]] == expected[1]


@pytest.mark.parametrize("kwargs", [{}, {"port": 1, "uplink": 0}])
async def test_set_beacon_needs_exactly_one_target(
    client: PiKVM, kwargs: dict[str, int]
) -> None:
    # kvmd checks port, then uplink, then falls through to downlink, so a
    # target-less call is a 400 rather than "all beacons off".
    with pytest.raises(ConfigurationError, match="exactly one of port, uplink"):
        await client.switch.set_beacon(False, **kwargs)


async def test_change_edid_without_changes(client: PiKVM) -> None:
    # kvmd skips the update and still answers ok, so the call would look
    # like it worked.
    with pytest.raises(ConfigurationError, match="needs a new name or new data"):
        await client.switch.change_edid("9b3c1e0a")


async def test_set_colors_without_roles(client: PiKVM) -> None:
    with pytest.raises(ConfigurationError, match="at least one role"):
        await client.switch.set_colors()


def test_a_colour_read_and_a_colour_written_are_different_types() -> None:
    """What the guide's colour converter rests on (#148).

    `state.colors` holds integer components while `set_colors()` takes
    `RRGGBB:BB:IIII` strings, so a colour read from the state cannot be handed
    back to the setter unchanged — which is why `switch.md` reformats it. The
    mistake that section warns about costs a round trip to find: httpx
    stringifies the model rather than refusing it, so `red=0 green=255 ...`
    goes out and kvmd answers 400 several layers from the call.
    """
    assert {f.annotation for f in SwitchColor.model_fields.values()} == {int}
    hints = get_type_hints(SwitchResource.set_colors)
    assert {hints[role] for role in ROLES} == {str | None}


async def test_set_port_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_port_params").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_port_params(0, name="Server1", dummy=True)
    url = str(mock_api.calls[-1].request.url)
    assert "port=0" in url
    assert "name=Server1" in url
    assert "dummy=1" in url


async def test_set_port_params_minimal(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/switch/set_port_params").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_port_params(2)
    url = str(mock_api.calls[-1].request.url)
    assert "port=2" in url
    assert "name" not in url


async def test_set_colors(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/set_colors").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.set_colors(beacon="FFA500:BF:0028", active="default")
    params = mock_api.calls[-1].request.url.params
    assert params["beacon"] == "FFA500:BF:0028"
    assert params["active"] == "default"
    assert "inactive" not in params


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/reset").mock(return_value=httpx.Response(200, json=OK))
    await client.switch.reset(0)
    url = str(mock_api.calls[-1].request.url)
    assert "unit=0" in url
    assert "bootloader" not in url


async def test_reset_with_bootloader(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/reset").mock(return_value=httpx.Response(200, json=OK))
    await client.switch.reset(1, bootloader=True)
    url = str(mock_api.calls[-1].request.url)
    assert "unit=1" in url
    assert "bootloader=1" in url


async def test_atx_power(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/atx/power").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.atx_power(0, "on")
    url = str(mock_api.calls[-1].request.url)
    assert "port=0" in url
    assert "action=on" in url


async def test_atx_click(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/switch/atx/click").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.switch.atx_click(3, "power")
    url = str(mock_api.calls[-1].request.url)
    assert "port=3" in url
    assert "button=power" in url
