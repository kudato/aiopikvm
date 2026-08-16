"""Contract tests — the models must handle what a real kvmd actually sends.

Payloads come from ``tests/fixtures/data``, captured from a live PiKVM running
kvmd 4.186. A case the library cannot handle today carries a strict ``xfail``
naming the issue that tracks it: the suite stays green while the gap is known,
and the moment a fix lands the stale marker fails instead of quietly hiding the
fact that the contract is now satisfied.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import pytest
from pydantic import BaseModel

from aiopikvm import (
    ATXState,
    GPIOState,
    HIDKeymaps,
    HIDState,
    MSDState,
    OCRInfo,
    StreamerState,
    SwitchState,
)
from aiopikvm.models.hid import _HIDInactivity
from tests.fixtures import DATA_DIR, load_jsonl, load_result, manifest
from tests.helpers import undeclared_fields


class Case(NamedTuple):
    """A captured response and the model that is supposed to describe it.

    Attributes:
        name: Capture name in the fixture manifest.
        model: Model the payload is validated against.
        key: Sub-key of the ``result`` payload holding the model's data.
        parse_issue: Issue number if the model cannot parse the capture.
        coverage_issue: Issue number if the model silently drops fields the
            capture contains.
    """

    name: str
    model: type[BaseModel]
    key: str | None = None
    parse_issue: int | None = None
    coverage_issue: int | None = None


CASES = (
    Case("atx", ATXState, coverage_issue=72),
    Case("hid", HIDState),
    Case("hid_keymaps", HIDKeymaps, key="keymaps"),
    Case("hid_inactivity", _HIDInactivity),
    Case("msd", MSDState, parse_issue=38, coverage_issue=38),
    Case("gpio", GPIOState, parse_issue=41, coverage_issue=41),
    Case("streamer", StreamerState, coverage_issue=52),
    Case("switch", SwitchState, parse_issue=42, coverage_issue=42),
    Case("streamer_ocr", OCRInfo, key="ocr"),
)


def _cases(field: str, reason: str) -> list[Any]:
    """Build parameters, marking cases that carry an issue number as xfail.

    Args:
        field: ``Case`` attribute holding the issue number for this test.
        reason: Human-readable failure reason, formatted with the issue.

    Returns:
        Parameter sets for :func:`pytest.mark.parametrize`.
    """
    params = []
    for case in CASES:
        issue = getattr(case, field)
        marks = (
            (pytest.mark.xfail(reason=f"{reason} (#{issue})", strict=True),)
            if issue
            else ()
        )
        params.append(pytest.param(case, marks=marks, id=case.name))
    return params


def _payload(case: Case) -> Any:
    """Return the captured payload the model is expected to accept."""
    result = load_result(case.name)
    return result if case.key is None else result[case.key]


@pytest.mark.parametrize(
    "case", _cases("parse_issue", "model cannot parse the kvmd 4.186 response")
)
def test_model_parses_captured_response(case: Case) -> None:
    """The model accepts the exact payload the device returned."""
    case.model.model_validate(_payload(case))


@pytest.mark.parametrize(
    "case", _cases("coverage_issue", "model drops fields kvmd 4.186 sends")
)
def test_model_declares_every_captured_field(case: Case) -> None:
    """Nothing the device sent lands in ``model_extra`` instead of a field."""
    assert undeclared_fields(case.model.model_validate(_payload(case))) == []


def test_manifest_matches_the_data_directory() -> None:
    """Every fixture file is listed in the manifest, and vice versa."""
    data = manifest()
    listed = {
        str(entry["file"])
        for entry in (*data["captures"].values(), *data["scenarios"].values())
    }
    present = {
        path.name for path in DATA_DIR.iterdir() if path.name != "_manifest.json"
    }
    assert listed == present


def test_manifest_device_matches_the_info_capture() -> None:
    """The recorded device metadata comes from the captured ``/api/info``."""
    info = load_result("info")
    assert manifest()["device"]["kvmd"] == info["system"]["kvmd"]["version"]
    assert manifest()["device"]["platform"] == info["hw"]["platform"]


def test_websocket_capture_holds_event_frames() -> None:
    """Every captured WebSocket message is an ``event_type``/``event`` frame."""
    events = load_jsonl("ws_events")
    assert events, "no WebSocket events captured"
    assert [entry["index"] for entry in events] == list(range(len(events)))
    assert all(
        set(entry["msg"]) == {"event_type", "event"}
        and isinstance(entry["msg"]["event_type"], str)
        for entry in events
    )
