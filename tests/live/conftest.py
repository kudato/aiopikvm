"""Fixtures for the live-device suite.

The device is configured through the environment so that credentials never
reach the repository:

    PIKVM_URL     device base URL, e.g. https://pikvm.local (required)
    PIKVM_USER    user name (default: admin)
    PIKVM_PASSWD  password (required)
    PIKVM_TOTP    TOTP code, appended to the password (optional)
"""

import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest

from aiopikvm import PiKVM


def _require(name: str) -> str:
    """Return an environment variable, skipping the test when it is unset."""
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is not set; the live suite needs a real device")
    return value


@pytest.fixture(autouse=True)
def no_machine_proxy() -> None:
    """Keep the machine's proxy, overriding the scrub in `tests/conftest.py`.

    The rest of the suite either mocks the network away or talks to a server
    on this machine, and wants the proxy environment gone. These reach
    whatever `PIKVM_URL` points at, which may only be reachable through it.

    `tests/live/test_isolation.py` is what keeps this honest.
    """


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


@pytest.fixture()
def live_client() -> Callable[..., AbstractAsyncContextManager[PiKVM]]:
    """Build a client against the same device with other settings.

    The `live` fixture covers the default; this is for the tests that have to
    compare it with something, an authentication mode above all.
    """

    def build(**kwargs: Any) -> AbstractAsyncContextManager[PiKVM]:
        return PiKVM(
            _require("PIKVM_URL"),
            user=os.environ.get("PIKVM_USER", "admin"),
            passwd=_require("PIKVM_PASSWD"),
            totp=os.environ.get("PIKVM_TOTP"),
            **kwargs,
        )

    return build
