"""Contract fixtures — responses captured from a real PiKVM device.

The payloads under ``data/`` are verbatim kvmd responses (sanitized, see
``README.md``) rather than hand-written dictionaries, so a mocked test can
never encode a shape the device does not produce.

Usage::

    from tests.fixtures import load_json, load_result

    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json=load_json("atx"))
    )
    state = ATXState.model_validate(load_result("atx"))
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "DATA_DIR",
    "load_json",
    "load_jsonl",
    "load_result",
    "load_text",
    "manifest",
]

DATA_DIR = Path(__file__).parent / "data"
MANIFEST_PATH = DATA_DIR / "_manifest.json"


def manifest() -> dict[str, Any]:
    """Return the capture manifest.

    Returns:
        The parsed ``_manifest.json``: the ``device`` the fixtures were
        captured from, the ``captures`` metadata (method, path, params,
        status, content type, file name) keyed by capture name, and the
        hand-recorded ``scenarios``.
    """
    data: dict[str, Any] = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return data


def _path(name: str) -> Path:
    """Resolve a capture or scenario name to its file.

    Args:
        name: Capture or scenario name from the manifest.

    Returns:
        Path to the fixture file.

    Raises:
        KeyError: If the name is not in the manifest.
    """
    data = manifest()
    entries = {**data["captures"], **data["scenarios"]}
    try:
        entry = entries[name]
    except KeyError:
        known = ", ".join(sorted(entries))
        raise KeyError(f"Unknown fixture {name!r}; available: {known}") from None
    return DATA_DIR / str(entry["file"])


def load_text(name: str) -> str:
    """Return a captured response as text.

    Args:
        name: Capture or scenario name from the manifest.

    Returns:
        The file contents verbatim.
    """
    return _path(name).read_text(encoding="utf-8")


def load_json(name: str) -> Any:
    """Return a captured JSON response, envelope included.

    Args:
        name: Capture or scenario name from the manifest.

    Returns:
        The parsed response body, e.g. ``{"ok": True, "result": {...}}``
        for kvmd API endpoints.
    """
    return json.loads(load_text(name))


def load_result(name: str) -> Any:
    """Return the ``result`` payload of a captured kvmd response.

    Args:
        name: Capture name from the manifest.

    Returns:
        The unwrapped ``result`` field of the response envelope.

    Raises:
        KeyError: If the capture is not an ``{"ok": ..., "result": ...}``
            envelope.
    """
    body = load_json(name)
    if not isinstance(body, dict) or "result" not in body:
        raise KeyError(f"Fixture {name!r} is not a kvmd response envelope")
    return body["result"]


def load_jsonl(name: str) -> list[Any]:
    """Return a captured JSON Lines file as a list of parsed lines.

    Args:
        name: Capture or scenario name from the manifest.

    Returns:
        One parsed JSON value per non-empty line.
    """
    return [json.loads(line) for line in load_text(name).splitlines() if line.strip()]
