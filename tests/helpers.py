"""Assertions shared by the mocked suite and the live-device suite."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

__all__ = ["undeclared_fields"]


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
