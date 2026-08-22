"""Test fixtures for aiopikvm."""

from collections.abc import AsyncIterator, Iterator

import pytest
import respx

from aiopikvm import PiKVM
from tests.helpers import scrub_proxy_environment


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the opt-in flags for the live-device suite."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="also run tests/live against the device configured in PIKVM_URL",
    )
    parser.addoption(
        "--live-mutating",
        action="store_true",
        default=False,
        help=(
            "also run the live tests that change device state; needs --live "
            "and PIKVM_MUTATING_OK set to the same URL as PIKVM_URL"
        ),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip what the opt-in flags have not asked for.

    The markers are what decide, not the path. ``item.keywords`` carries the
    names of every parent node, and the directory is called ``live``, so
    testing against it would skip anything under `tests/live` whether it
    needs a device or not — `tests/live/test_isolation.py` needs none.

    ``mutating`` is the second gate. Everything carrying it also carries
    ``live``, so ``--live-mutating`` alone collects nothing: a run that
    changes somebody's device has to say both.
    """
    live = config.getoption("--live")
    mutating = config.getoption("--live-mutating")
    skip_live = pytest.mark.skip(reason="needs --live and a reachable PiKVM")
    skip_mutating = pytest.mark.skip(
        reason="changes device state; needs --live --live-mutating"
    )
    for item in items:
        if not live and item.get_closest_marker("live") is not None:
            item.add_marker(skip_live)
        if not mutating and item.get_closest_marker("mutating") is not None:
            item.add_marker(skip_mutating)


@pytest.fixture(autouse=True)
def no_machine_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the machine's proxy from every test outside `tests/live`.

    `tests/test_ws.py` starts a real server on this machine and points a real
    *websockets* client at it. A developer who works behind a proxy watches
    the client dial the proxy instead, and the assertions about what it
    connected to stop holding, with nothing in the output to say the
    environment is why. `scrub_proxy_environment` covers what it takes to
    make this stick.

    `tests/live` overrides this fixture with one that does nothing: those talk
    to a real device, which may only be reachable through the very proxy this
    one throws away.
    """
    scrub_proxy_environment(monkeypatch)


@pytest.fixture()
def mock_api() -> Iterator[respx.MockRouter]:
    """Mock router for httpx."""
    with respx.mock(base_url="https://pikvm.local") as router:
        yield router


@pytest.fixture()
async def client(mock_api: respx.MockRouter) -> AsyncIterator[PiKVM]:
    """PiKVM client with mocked API."""
    async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as c:
        yield c
