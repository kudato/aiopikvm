"""Read-only smoke tests against a real PiKVM.

Collected only with ``pytest --live`` and skipped unless the device is
configured in the environment (see ``conftest.py``).

Everything here must stay read-only. The device under test is somebody's
working KVM wired to a real machine: an ATX call power-cycles their host, an
MSD call rewrites a virtual drive, HID input types into whatever window has
focus. Anything that changes device state belongs in a manual check, not here.

A failure is a real gap between this client and that device — check the open
issues before assuming the harness is at fault. One exception: kvmd itself is
not always stable under the WebSocket churn this file produces. It has been
seen to die of SIGSEGV inside CPython while a session connects and the
streamer is stopping, and systemd restarts it a second or two later; the tests
that were opening a socket at that moment fail with an HTTP 502 from the nginx
in front of it. That is the device's own bug, not this client's — but it does
mean every socket opened here has a cost, so open as few as the check needs.
"""

import asyncio
import contextlib

import pytest

from aiopikvm import (
    APIError,
    ATXState,
    AuthError,
    HIDState,
    MSDState,
    PiKVM,
    PiKVMWebSocket,
    StreamerState,
)
from aiopikvm.resources.redfish import RESET_TYPES
from tests.helpers import undeclared_fields

pytestmark = pytest.mark.live

SUBSYSTEMS = ("atx", "hid", "msd", "gpio", "streamer", "switch")

WS_TIMEOUT = 5.0
"""How long to wait for the initial WebSocket events."""

WS_EXPECTED = frozenset({"loop", "atx", "msd", "streamer"})
"""Event types kvmd pushes right after the handshake, in no fixed order."""

WS_STATES = frozenset({"atx", "msd", "streamer", "hid"})
"""The same, without the events that say nothing about the device."""


@pytest.mark.parametrize("subsystem", SUBSYSTEMS)
async def test_get_state_parses(live: PiKVM, subsystem: str) -> None:
    """``get_state()`` accepts what the device actually returns."""
    await getattr(live, subsystem).get_state()


@pytest.mark.parametrize("subsystem", SUBSYSTEMS)
async def test_state_declares_every_field(live: PiKVM, subsystem: str) -> None:
    """The device sends no field the models leave undeclared."""
    state = await getattr(live, subsystem).get_state()
    assert undeclared_fields(state) == []


async def test_info_reports_versions(live: PiKVM) -> None:
    """``/api/info`` exposes the kvmd and streamer versions."""
    info = await live.system.get_info()
    assert info["system"]["kvmd"]["version"]
    assert info["system"]["streamer"]["version"]


async def test_info_honours_the_field_filter(live: PiKVM) -> None:
    """Several ``fields`` values are all applied, not just the first one."""
    info = await live.system.get_info("hw", "system")
    assert set(info) == {"hw", "system"}


async def test_log_returns_text(live: PiKVM) -> None:
    """``/api/log`` returns plain text for a bounded time window."""
    assert await live.system.get_log(seek=60) is not None


async def test_prometheus_metrics_are_exposition_format(live: PiKVM) -> None:
    """The metrics export is a Prometheus text exposition document."""
    metrics = await live.prometheus.get_metrics()
    assert "# TYPE pikvm_" in metrics


async def test_prometheus_omits_help_lines(live: PiKVM) -> None:
    """kvmd emits ``# TYPE`` only; the docs used to promise ``# HELP`` (#78)."""
    metrics = await live.prometheus.get_metrics()
    comments = [line for line in metrics.splitlines() if line.startswith("#")]
    assert comments
    assert all(line.startswith("# TYPE ") for line in comments)


async def test_keymaps_list_the_default_layout(live: PiKVM) -> None:
    """The device's keymap catalogue includes the layout it defaults to."""
    keymaps = await live.hid.get_keymaps()
    assert keymaps.default in keymaps.available


async def test_inactivity_is_a_counter(live: PiKVM) -> None:
    """``/api/hid/inactivity`` reports elapsed seconds, never a negative."""
    assert await live.hid.get_inactivity() >= 0


async def test_ocr_info_lists_languages(live: PiKVM) -> None:
    """OCR capability metadata parses into :class:`OCRInfo`."""
    info = await live.streamer.get_ocr_info()
    assert info.langs.available


async def test_redfish_root_links_to_systems(live: PiKVM) -> None:
    """The Redfish service root advertises the Systems collection."""
    root = await live.redfish.get_root()
    assert root["Systems"]["@odata.id"].endswith("/Systems")


async def test_redfish_system_id_is_a_string(live: PiKVM) -> None:
    """``"0"`` resolves and ``"1"`` does not: the id is compared as text (#57)."""
    system = await live.redfish.get_system()
    assert system["Id"] == "0"
    with pytest.raises(APIError) as info:
        await live.redfish.get_system("1")
    assert info.value.status_code == 400


async def test_redfish_reset_types_match_the_constant(live: PiKVM) -> None:
    """The device's allowable values are exactly :data:`RESET_TYPES` (#78)."""
    system = await live.redfish.get_system()
    allowed = system["Actions"]["#ComputerSystem.Reset"][
        "ResetType@Redfish.AllowableValues"
    ]
    assert sorted(allowed) == sorted(RESET_TYPES)


async def test_redfish_refuses_an_unknown_reset_type(live: PiKVM) -> None:
    """A refused reset stays inside the exception hierarchy.

    This is the one POST in this file, and it changes nothing: kvmd validates
    ``ResetType`` against its list before dispatching anything, and the value
    sent here is deliberately one no version could ever implement.
    """
    with pytest.raises(APIError) as info:
        await live.redfish.reset("aiopikvm-does-not-exist")
    assert info.value.status_code == 400


async def test_msd_remote_upload_refuses_an_unusable_url(live: PiKVM) -> None:
    """A refused remote upload is a status, not a record in the stream (#40).

    This is a POST, and it writes nothing: kvmd validates the URL before it
    fetches anything, and the scheme sent here is one it never accepts.
    """
    with pytest.raises(APIError) as caught:
        await live.msd.upload_remote("ftp://localhost/aiopikvm-does-not-exist.iso")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"


async def test_websocket_delivers_the_initial_state(live: PiKVM) -> None:
    """Every subsystem sends its state once the socket is open, ``loop`` first."""
    seen: list[str] = []
    async with live.ws() as ws:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(WS_TIMEOUT):
                async for event in ws.events():
                    seen.append(event["event_type"])
                    if WS_EXPECTED <= set(seen):
                        break
    assert WS_EXPECTED <= set(seen)
    assert seen[0] == "loop", "the protocol version always comes first"


@pytest.mark.parametrize("binary", [False, True])
async def test_websocket_ping_is_answered(live: PiKVM, binary: bool) -> None:
    """Both channels answer, and the round trip is a real measurement (#82).

    The version is checked on the same socket rather than on one of its own:
    every WebSocket this suite opens is a connect and a disconnect for kvmd to
    survive, and it does not always (see the module docstring).
    """
    reported = (await live.system.get_info("system"))["system"]["kvmd"]["version"]
    async with live.ws(stream=False, binary=binary) as ws:
        latency = await ws.ping(timeout=WS_TIMEOUT)
        assert 0 < latency < WS_TIMEOUT
        # Reading the pong means reading past the loop event that precedes it.
        assert ws.version is not None
        assert f"{ws.version.major}.{ws.version.minor}" == reported


async def test_websocket_states_type_what_the_device_sends(live: PiKVM) -> None:
    """The socket's payloads go through the REST models on a real device (#61)."""
    seen: dict[str, object] = {}
    async with live.ws(stream=False) as ws:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(WS_TIMEOUT):
                async for state in ws.states():
                    seen[state.updated] = getattr(state, state.updated)
                    if WS_STATES <= seen.keys():
                        break
    assert WS_STATES <= seen.keys()
    assert isinstance(seen["atx"], ATXState)
    assert isinstance(seen["msd"], MSDState)
    assert isinstance(seen["streamer"], StreamerState)
    assert isinstance(seen["hid"], HIDState)


async def test_hid_refuses_a_key_it_has_no_name_for(live: PiKVM) -> None:
    """A name outside ``KEY_NAMES`` is refused before anything is typed (#77).

    Two POSTs that press nothing: kvmd runs every key through
    ``valid_hid_key`` before it touches the HID, and ``"NoSuchKey"`` is a name
    no version has. It is deliberately short — the validator refuses anything
    over 16 characters on length alone, with a message that names no key.

    The shortcut is the one worth asking about, and the answer is not in the
    response: a kvmd that pressed ``ControlLeft`` first and only then refused
    would return the same 400 and the same message. The inactivity counter is
    what tells them apart, since kvmd bumps it from inside the HID call — an
    unchanged one is the device saying nothing was pressed.
    """
    if (await live.hid.get_state()).jiggler.active:
        pytest.skip("the jiggler resets the inactivity counter on its own")
    before = await live.hid.get_inactivity()
    assert before > 0, "nothing may have touched the HID just before this"

    with pytest.raises(APIError) as caught:
        await live.hid.send_key("NoSuchKey")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"
    assert "NoSuchKey" in (caught.value.error_msg or "")

    with pytest.raises(APIError) as caught:
        await live.hid.send_shortcut("ControlLeft", "NoSuchKey")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"

    assert await live.hid.get_inactivity() >= before, (
        "the refused shortcut pressed something: kvmd validates the list "
        "before it presses any of it, so the counter must not have reset"
    )


async def test_hid_mouse_reports_which_motion_it_takes(live: PiKVM) -> None:
    """Only one of the two motion events does anything, and this says which.

    kvmd drops a relative event while the mouse is absolute and an absolute one
    while it is relative, both without telling the sender, so a caller has to
    read the mode rather than try it (#60). Nothing is sent here — the check is
    that the state carries the answer.
    """
    state = await live.hid.get_state()
    assert isinstance(state.mouse.absolute, bool)
    assert state.mouse.outputs is not None
    assert state.mouse.outputs.active in state.mouse.outputs.available


async def _client_counts(ws: PiKVMWebSocket, seconds: float = 2.0) -> list[int]:
    """Collect the ``clients`` counts kvmd broadcasts on *ws*.

    kvmd broadcasts one when the stream controller wakes and one from the
    connect handler, so a single read can return a count from before the
    thing being measured. This drains the window instead.

    Args:
        ws: An open socket.
        seconds: How long to listen.

    Returns:
        Every count seen, in arrival order.
    """
    counts: list[int] = []
    events = ws.events()
    try:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                async for event in events:
                    if event["event_type"] == "clients":
                        counts.append(event["event"]["count"])
    finally:
        await events.aclose()
    return counts


async def test_websocket_stream_flag_counts_this_client(live: PiKVM) -> None:
    """``stream=True`` is what makes kvmd count the session as a video viewer."""
    # Every count is read from the same watching socket, so a viewer that
    # comes or goes elsewhere shows up as its own event rather than as a
    # wrong delta. The watcher itself must not be counted.
    async with live.ws(stream=False) as watcher:
        settled = await _client_counts(watcher)
        assert settled, "kvmd broadcasts a clients count when a session connects"
        baseline = settled[-1]

        async with live.ws(stream=True):
            with_viewer = await _client_counts(watcher)
        assert baseline + 1 in with_viewer, "a stream client is counted"

        async with live.ws(stream=False):
            without_viewer = await _client_counts(watcher)
        assert without_viewer, "kvmd broadcasts a clients count on every connect"
        assert baseline + 1 not in without_viewer, "a non-stream client is not"

    assert without_viewer[-1] == baseline


async def test_websocket_refuses_a_wrong_password(live: PiKVM) -> None:
    """kvmd applies the auth chain to the upgrade, and this reports it as such."""
    async with PiKVM(
        str(live.base_url), user="admin", passwd="definitely-not-the-password"
    ) as wrong:
        with pytest.raises(AuthError) as caught:
            async with wrong.ws():
                pass
    assert caught.value.status_code == 403
    assert caught.value.error == "ForbiddenError"


async def test_websocket_rejects_an_unparsable_stream_flag(live: PiKVM) -> None:
    """A 400 from the upgrade is a plain APIError, not an auth failure."""
    ws = live.ws()
    ws._url = ws._url.replace("stream=1", "stream=nonsense")
    with pytest.raises(APIError) as caught:
        async with ws:
            pass
    assert not isinstance(caught.value, AuthError)
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"
