"""Fixtures for the live-device suite.

The device is configured through the environment so that credentials never
reach the repository:

    PIKVM_URL     device base URL, e.g. https://pikvm.local (required)
    PIKVM_USER    user name (default: admin)
    PIKVM_PASSWD  password (required)
    PIKVM_TOTP    TOTP code, appended to the password (optional)

`test_mutating.py` needs more than that, because it changes the device:

    PIKVM_MUTATING_OK      must equal PIKVM_URL, character for character
    PIKVM_MUTATING_MSD     also run the mass storage lifecycle
    PIKVM_MUTATING_GPIO    also toggle a GPIO output away from its state
    PIKVM_MUTATING_LOGOUT  also run the logout test, which closes every
                           session of the user, browser tabs included
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
async def mutable(live: PiKVM) -> PiKVM:
    """The same client, once the device has been named as expendable.

    `--live-mutating` says "run the tests that change something".
    ``PIKVM_MUTATING_OK`` says *which device* they may change, and it has to
    match ``PIKVM_URL`` character for character. A flag on its own is one
    shell-history recall away from power-cycling the wrong machine; a URL
    that has to be typed out again is not.
    """
    allowed = os.environ.get("PIKVM_MUTATING_OK", "")
    if not allowed:
        pytest.skip(
            "PIKVM_MUTATING_OK is not set; these tests change device state "
            "and will not guess which device may be changed"
        )
    if allowed != os.environ.get("PIKVM_URL", ""):
        pytest.skip(
            f"PIKVM_MUTATING_OK ({allowed!r}) is not PIKVM_URL; refusing to "
            "change a device that was not named"
        )
    return live


def opt_in(name: str) -> None:
    """Skip unless the named opt-in variable is set to something truthy."""
    if os.environ.get(name, "").strip().lower() in ("", "0", "false", "no"):
        pytest.skip(f"{name} is not set")


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
