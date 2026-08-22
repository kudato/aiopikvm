"""The live suite is the one place that keeps the machine's proxy.

`tests/conftest.py` scrubs the proxy environment for every test;
`tests/live/conftest.py` shadows that fixture with an empty one, because the
device may only be reachable through the proxy the scrub takes away. Nothing
else pins that override down.

This carries no ``live`` marker on purpose. It needs no device, and a check
that only ran under `--live` would sit out every CI run and protect nobody.
"""

import os

import pytest

# Read while this module is imported, which is during collection and so
# before any function-scoped fixture can run: this is what the machine has,
# not what the suite left behind.
MACHINE_NO_PROXY = os.environ.get("no_proxy")


def test_the_scrub_does_not_reach_the_live_suite() -> None:
    """Delete the override in `tests/live/conftest.py` and this fails."""
    if MACHINE_NO_PROXY == "*":
        pytest.skip("this machine sets no_proxy=* itself; nothing to tell apart")
    assert os.environ.get("no_proxy") != "*"
