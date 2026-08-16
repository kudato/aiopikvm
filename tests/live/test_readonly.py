"""Read-only smoke tests against a real PiKVM.

Collected only with ``pytest --live`` and skipped unless the device is
configured in the environment (see ``conftest.py``).

Everything here must stay read-only. The device under test is somebody's
working KVM wired to a real machine: an ATX call power-cycles their host, an
MSD call rewrites a virtual drive, HID input types into whatever window has
focus. Anything that changes device state belongs in a manual check, not here.

A failure is a real gap between this client and that device — check the open
issues before assuming the harness is at fault.
"""

import asyncio
import contextlib

import pytest

from aiopikvm import PiKVM
from tests.helpers import undeclared_fields

pytestmark = pytest.mark.live

SUBSYSTEMS = ("atx", "hid", "msd", "gpio", "streamer", "switch")

WS_TIMEOUT = 5.0
"""How long to wait for the initial WebSocket state bundle."""

WS_EXPECTED = frozenset({"loop", "atx", "msd", "streamer"})
"""Event types kvmd pushes right after the handshake."""


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


async def test_websocket_delivers_the_initial_state(live: PiKVM) -> None:
    """The event socket pushes a full state bundle right after connecting."""
    seen: set[str] = set()
    async with live.ws() as ws:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(WS_TIMEOUT):
                async for event in ws.events():
                    seen.add(event["event_type"])
                    if WS_EXPECTED <= seen:
                        break
    assert WS_EXPECTED <= seen
