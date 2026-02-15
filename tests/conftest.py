"""Test fixtures for aiopikvm."""

from collections.abc import AsyncIterator, Iterator

import pytest
import respx

from aiopikvm import PiKVM


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
