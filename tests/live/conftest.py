"""Fixtures for the live-device suite.

The device is configured through the environment so that credentials never
reach the repository:

    PIKVM_URL     device base URL, e.g. https://pikvm.local (required)
    PIKVM_USER    user name (default: admin)
    PIKVM_PASSWD  password (required)
    PIKVM_TOTP    TOTP code, appended to the password (optional)
"""

import os
from collections.abc import AsyncIterator

import pytest

from aiopikvm import PiKVM


def _require(name: str) -> str:
    """Return an environment variable, skipping the test when it is unset."""
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is not set; the live suite needs a real device")
    return value


@pytest.fixture()
async def live() -> AsyncIterator[PiKVM]:
    """Client connected to the real device."""
    async with PiKVM(
        _require("PIKVM_URL"),
        user=os.environ.get("PIKVM_USER", "admin"),
        passwd=_require("PIKVM_PASSWD"),
        totp=os.environ.get("PIKVM_TOTP"),
    ) as kvm:
        yield kvm
