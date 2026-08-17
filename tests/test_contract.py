"""Contract tests — the models must handle what a real kvmd actually sends.

Payloads come from ``tests/fixtures/data``, captured from a live PiKVM running
kvmd 4.186. A case the library cannot handle today carries a strict ``xfail``
naming the issue that tracks it: the suite stays green while the gap is known,
and the moment a fix lands the stale marker fails instead of quietly hiding the
fact that the contract is now satisfied.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple, get_args

import pytest
from pydantic import BaseModel

from aiopikvm import (
    ATXState,
    GPIOState,
    HIDKeymaps,
    HIDState,
    MSDState,
    MSDUpload,
    OCRInfo,
    StreamerState,
    SwitchState,
)
from aiopikvm._ws import _STATE_MODELS, _as_state, _merge
from aiopikvm.models.hid import _HIDInactivity
from aiopikvm.resources.hid import KEY_NAMES, KeyboardOutput, MouseButton, MouseOutput
from aiopikvm.resources.msd import Compression
from aiopikvm.resources.redfish import ResetType
from aiopikvm.resources.switch import ATXAction, ATXButton
from tests.fixtures import DATA_DIR, load_json, load_jsonl, load_result, manifest
from tests.helpers import undeclared_fields

ROOT = Path(__file__).parent.parent


def _prose() -> list[Path]:
    """Return the published prose: the whole docs tree and the README."""
    return [*ROOT.joinpath("docs").rglob("*.md"), ROOT / "README.md"]


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
    Case("atx", ATXState),
    Case("hid", HIDState),
    Case("hid_keymaps", HIDKeymaps, key="keymaps"),
    Case("hid_inactivity", _HIDInactivity),
    Case("msd", MSDState),
    Case("msd_online", MSDState),
    Case("msd_image", MSDState),
    Case("msd_uploading", MSDState),
    Case("msd_downloading", MSDState),
    Case("gpio", GPIOState),
    Case("streamer", StreamerState),
    Case("switch", SwitchState),
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


def test_write_info_parses_into_the_upload_model() -> None:
    """Every write info the device sent parses, from both write endpoints.

    ``/api/msd/write`` sends one per response and ``/api/msd/write_remote``
    one per line of its NDJSON stream, and both go through the same model.
    """
    blocks: dict[str, list[Any]] = {}
    for entry in load_json("msd_write")["steps"]:
        if entry["status"] != 200:
            continue
        if "body" in entry:
            records = [entry["body"]]
        else:
            records = [
                json.loads(line)
                for line in str(entry["body_text"]).splitlines()
                if line
            ]
        found = [record["result"]["image"] for record in records if record["ok"]]
        assert found, f"{entry['name']} answered 200 with no write info"
        blocks[entry["name"]] = found

    assert set(blocks) == {"write_ok", "write_prefix", "remote_ok", "remote_broken"}
    for name, found in blocks.items():
        for block in found:
            info = MSDUpload.model_validate(block)
            assert undeclared_fields(info) == [], name


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


def test_every_captured_event_parses_into_its_model() -> None:
    """What the socket broadcasts is what the REST models describe (#61).

    Each event is merged into what the same subsystem sent before, the way
    ``PiKVMWebSocket.states`` does, because kvmd sends a subsystem in full
    once and then only the parts of it that change.
    """
    seen: dict[str, dict[str, Any]] = {}
    parsed: set[str] = set()
    for entry in load_jsonl("ws_events"):
        event_type = str(entry["msg"]["event_type"])
        if event_type not in _STATE_MODELS:
            continue
        merged = _merge(seen.get(event_type, {}), entry["msg"]["event"])
        seen[event_type] = merged
        state = _as_state(event_type, merged)
        assert undeclared_fields(state) == [], f"{event_type} at {entry['index']}"
        parsed.add(event_type)

    assert parsed == set(_STATE_MODELS), (
        "the capture must exercise every subsystem the states API types"
    )


def test_key_names_match_the_device_table() -> None:
    """The exported catalogue is kvmd's own table, name for name (#77).

    kvmd exposes it through no endpoint, so the fixture is the table read off
    the device itself and this is what keeps the copy in the library honest.
    """
    assert KEY_NAMES == set(load_json("hid_keys")["keys"])
    # The docs and the changelog quote the count; pin it so refreshing the
    # fixture from a newer kvmd cannot leave them quietly wrong.
    assert len(KEY_NAMES) == 115


def test_documented_key_names_are_ones_kvmd_accepts() -> None:
    """No example in the docs or the README types a key that does not exist.

    A wrong name in an example fails with HTTP 400 over HTTP and with nothing
    at all over the WebSocket, which is exactly the failure this catalogue
    exists to prevent (#77).
    """
    calls = re.compile(r"send_(?:key|shortcut)\(([^)]*)\)")
    names = {
        name
        for path in _prose()
        for call in calls.findall(path.read_text(encoding="utf-8"))
        for name in re.findall(r'"([^"]*)"', call)
    }
    assert names, "no key name found in the docs; the pattern stopped matching"
    assert names <= KEY_NAMES, sorted(names - KEY_NAMES)


_TYPED_VALUES = (
    ("keyboard_output", r'keyboard_output="([^"\n]*)"', KeyboardOutput),
    ("mouse_output", r'mouse_output="([^"\n]*)"', MouseOutput),
    ("mouse button", r'send_mouse_button\(\s*"([^"\n]*)"', MouseButton),
    ("compression", r'compress="([^"\n]*)"', Compression),
    # The ATX pair take the name positionally, so the match has to walk the
    # argument list — but only as far as the end of the call, and never past
    # the end of the line, or the capture runs on into the next quoted thing
    # in the file.
    ("ATX action", r'atx_power\([^)\n]*"([^"\n]*)"', ATXAction),
    ("ATX button", r'atx_click\([^)\n]*"([^"\n]*)"', ATXButton),
)
_TYPE_TABLE = ROOT / "docs" / "guide" / "error-handling.md"


@pytest.mark.parametrize(
    ("what", "pattern", "alias"), _TYPED_VALUES, ids=[case[0] for case in _TYPED_VALUES]
)
def test_documented_values_are_ones_the_type_allows(
    what: str, pattern: str, alias: Any
) -> None:
    """No example types a value its own parameter would reject (#68).

    A type checker reads the library, not the prose, so an example is the
    one place one of these vocabularies can go wrong unnoticed — and it is
    the place a reader copies from. Each pattern has to match something:
    a rename that stops it matching fails here rather than passing on an
    empty set.
    """
    found = {
        value
        for path in _prose()
        for value in re.findall(pattern, path.read_text(encoding="utf-8"))
    }
    assert found, f"no {what} found in the docs; the pattern stopped matching"
    allowed = set(get_args(alias.__value__))
    assert found <= allowed, sorted(found - allowed)


_ALIASES = {
    "KeyboardOutput": KeyboardOutput,
    "MouseOutput": MouseOutput,
    "MouseButton": MouseButton,
    "Compression": Compression,
    "ATXAction": ATXAction,
    "ATXButton": ATXButton,
    "ResetType": ResetType,
}


def _type_table() -> dict[str, list[str]]:
    """Parse the guide's table of typed values into ``{type: [value, ...]}``.

    The table is the one place every value of every vocabulary is written
    out — the row a reader copies from — and the scan above cannot see it,
    since nothing in it is shaped like a call.
    """
    rows: dict[str, list[str]] = {}
    for line in _TYPE_TABLE.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 3 or cells[1].strip("`") not in _ALIASES:
            continue
        rows[cells[1].strip("`")] = [
            # The empty compression mode is spelled `""` in the table.
            "" if value == '""' else value
            for value in re.findall(r"`([^`]*)`", cells[2])
        ]
    return rows


def test_the_guide_lists_every_typed_vocabulary() -> None:
    """A type nobody documented is one nobody knows to use (#68)."""
    assert set(_type_table()) == set(_ALIASES)


@pytest.mark.parametrize("name", sorted(_ALIASES))
def test_the_guide_spells_a_vocabulary_out_in_full(name: str) -> None:
    """Each row of that table is its type's values, all of them (#68).

    Equality, not containment: a value the table invents misleads as badly
    as one it leaves out, and both are invisible to a type checker, which
    reads the library and not the prose.
    """
    listed = _type_table()[name]
    assert len(listed) == len(set(listed)), f"the guide repeats a value: {listed}"
    assert set(listed) == set(get_args(_ALIASES[name].__value__))


def test_mouse_outputs_the_device_offers_are_ones_the_client_types() -> None:
    """What a real kvmd advertises as switchable is inside ``MouseOutput``.

    The one direction a capture can prove: the outputs kvmd lists for the
    mouse it is running have to be values this client will let a caller ask
    for. The reverse is not checkable here — a capture shows the backend's
    own subset, not everything kvmd's validator accepts. The keyboard side
    is left out for that reason: it is empty under ``otg``, so a subset
    assertion on it would hold whatever the type said (#68).
    """
    available = load_result("hid")["mouse"]["outputs"]["available"]
    assert available, "the capture advertises no mouse output to check against"
    assert set(available) <= set(get_args(MouseOutput.__value__))


def test_the_capture_contains_a_partial_update() -> None:
    """Without one, the merge above would prove nothing (#61)."""
    keys: dict[str, list[set[str]]] = {}
    for entry in load_jsonl("ws_events"):
        event_type = str(entry["msg"]["event_type"])
        keys.setdefault(event_type, []).append(set(entry["msg"]["event"]))
    assert any(
        later < first
        for sent in keys.values()
        for first in [sent[0]]
        for later in sent[1:]
    ), "no event carries a subset of the keys the first one did"
