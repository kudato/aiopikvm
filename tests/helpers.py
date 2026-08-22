"""Helpers shared across the test suite.

`undeclared_fields` is an assertion used by both the mocked suite and the
live-device suite. `scrub_proxy_environment` is the opposite shape: every
test uses it except the live ones, which need the environment it takes away.
`defaults` is read by the tests that compare one signature against another.
"""

from __future__ import annotations

import inspect
import os
from typing import Any

import pytest
from pydantic import BaseModel

__all__ = ["defaults", "scrub_proxy_environment", "undeclared_fields"]


def defaults(func: Any) -> dict[str, Any]:
    """Collect the default values a callable's signature declares.

    Several defaults in this package are spelled in two places — a factory
    method and the class it builds, a helper and the connector under it — and
    the tests that keep those in step compare signatures rather than
    behaviour, since a default that is merely restated has no behaviour of
    its own until it drifts.

    Args:
        func: Callable to read.

    Returns:
        Each parameter that has a default, mapped to it. Parameters without
        one are left out rather than given a placeholder.
    """
    return {
        name: parameter.default
        for name, parameter in inspect.signature(func).parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }


def scrub_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the machine's proxy configuration away from a test.

    *websockets* resolves a proxy from the environment itself, through
    `urllib.request`, unless the caller passes `proxy=` or connects over
    `sock`/`unix`. This client passes none of those, so `tests/test_ws.py`
    dials whatever the machine has configured instead of the server it just
    started.

    ``no_proxy=*`` is the line that stops it: `urllib.request.proxy_bypass()`
    reads the star as a blanket bypass and answers before any proxy is looked
    up. Setting it also does what deleting cannot — on macOS and Windows
    `urllib.request.getproxies()` falls back to the operating system's own
    settings when the environment names no proxy at all, so an empty
    environment uncovers a system-wide proxy rather than hiding one. (Linux
    has no such fallback; `getproxies` there is `getproxies_environment`.)

    The variables go too, in every spelling `getproxies_environment()` scans
    for. No test here needs that today, and removing the loop leaves the suite
    green: it is so that anything reading ``HTTPS_PROXY`` on its own, without
    honouring ``no_proxy``, also sees a machine with no proxy on it.

    Args:
        monkeypatch: Patcher whose scope decides how long this lasts.
    """
    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")


def undeclared_fields(value: Any, path: str = "") -> list[str]:
    """Collect fields present in a parsed response but not declared by a model.

    Models allow extra fields so that a newer kvmd cannot break parsing; the
    flip side is that a field nobody declared is invisible to users instead of
    being a loud error. This walks a validated model and reports everything
    that ended up in ``model_extra``.

    Args:
        value: A validated model, or any value reachable from one.
        path: Dotted path of *value* within the top-level model.

    Returns:
        Dotted paths of every undeclared field, in traversal order.
    """
    found: list[str] = []
    if isinstance(value, BaseModel):
        found += [_join(path, key) for key in value.model_extra or {}]
        for name in type(value).model_fields:
            found += undeclared_fields(getattr(value, name), _join(path, name))
    elif isinstance(value, dict):
        for key, item in value.items():
            found += undeclared_fields(item, _join(path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += undeclared_fields(item, f"{path}[{index}]")
    return found


def _join(path: str, key: str) -> str:
    """Append *key* to a dotted *path*."""
    return f"{path}.{key}" if path else key
