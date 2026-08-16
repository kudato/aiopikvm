"""Test fixtures for aiopikvm."""

from collections.abc import AsyncIterator, Iterator

import pytest
import respx

from aiopikvm import PiKVM


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the opt-in flag for the live-device suite."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="also run tests/live against the device configured in PIKVM_URL",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip everything marked ``live`` unless ``--live`` was given."""
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live and a reachable PiKVM")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


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
