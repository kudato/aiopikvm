"""Contract tests — the models must handle what a real kvmd actually sends.

Payloads come from ``tests/fixtures/data``, captured from a live PiKVM running
kvmd 4.206. A case the library cannot handle today carries a strict ``xfail``
naming the issue that tracks it: the suite stays green while the gap is known,
and the moment a fix lands the stale marker fails instead of quietly hiding the
fact that the contract is now satisfied.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, NamedTuple, get_args

import pytest
from pydantic import BaseModel

from aiopikvm import (
    ATXState,
    DeviceState,
    GPIOState,
    HIDKeymaps,
    HIDState,
    InfoState,
    MediaState,
    MSDState,
    MSDUpload,
    OCRInfo,
    Streamer,
    StreamerState,
    SwitchState,
)
from aiopikvm._ws import _STATE_MODELS, _as_state, _merge
from aiopikvm.models.hid import _HIDInactivity
from aiopikvm.resources.hid import KEY_NAMES, MouseOutput
from tests.fixtures import DATA_DIR, load_json, load_jsonl, load_result, manifest
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
    Case("info_legacy0", InfoState),
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
    "case", _cases("parse_issue", "model cannot parse the kvmd 4.206 response")
)
def test_model_parses_captured_response(case: Case) -> None:
    """The model accepts the exact payload the device returned."""
    case.model.model_validate(_payload(case))


@pytest.mark.parametrize(
    "case", _cases("coverage_issue", "model drops fields kvmd 4.206 sends")
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


def test_no_recording_sits_beside_the_data_directory() -> None:
    """A recorded payload belongs under `data/`, which is where they load from.

    One recorder wrote a level above it. Running it printed a path and
    changed nothing the suite loads, and the file it left there was committed
    and went stale — a second `janus_session.json`, a different session, read
    by nobody (#142).

    The tools themselves live at that level, and so does the TLS material
    `test_tls.py` reads from `tls/`, so this is not "nothing but `data/`": it
    is that nothing which looks like a recording may sit outside it. Any file
    that is not one of the tools and not this README is one.
    """
    root = DATA_DIR.parent
    stray = sorted(
        path.name
        for path in root.iterdir()
        if path.is_file() and path.suffix != ".py" and path.name != "README.md"
    )
    assert stray == []


@pytest.mark.parametrize(
    ("recorder", "scenario"),
    [("record_janus", "janus_session"), ("record_media", "media_stream")],
)
def test_the_recorders_write_where_the_loader_reads(
    recorder: str, scenario: str
) -> None:
    """Both write the name the manifest gives, under `data/` (#142).

    Read off the source rather than by running them: recording needs a real
    device, and a recorder that silently writes nowhere spends the owner's
    permission for nothing. What that cannot see is everything between the
    path and the write — this pins the expression, not the file appearing.

    Args:
        recorder: Module name of the recorder, beside `data/`.
        scenario: Manifest key of the scenario it records.
    """
    source = (DATA_DIR.parent / f"{recorder}.py").read_text(encoding="utf-8")
    listed = manifest()["scenarios"][scenario]["file"]
    assert f'DATA_DIR / "{listed}"' in source
    # Not a spelling of the old bug: every way of reaching for this file's own
    # directory goes through __file__, and neither recorder needs it for
    # anything else.
    assert "__file__" not in source


_DOCUMENTATION_ONLY = {
    ("media_stream", "stream_bad_flag"),
    ("janus_session", "session_events"),
    ("janus_session", "after_stop"),
}
"""Recorded steps no test replays, and cannot: one describes a request this
client has no way to make, and two recorded that the device sent nothing at
all. Everything else in these two scenarios is a mock waiting to be used, and
a step nobody loads is a claim nobody checks (#144)."""


@pytest.mark.parametrize("scenario", ["media_stream", "janus_session"])
def test_every_recorded_step_is_used_or_says_why_not(scenario: str) -> None:
    """A step with no consumer is documentation, and has to admit it (#144).

    Seven steps of these two scenarios had drifted into that state
    unannounced, one carrying the executable-sounding claim that it "parses
    fine" — which nothing executed. The other five scenario files are not
    checked here: their steps carry ``note`` rather than ``description``, and
    some of them are orphaned too, which is its own cleanup and not this
    test's.

    A step counts as used when some test names it as a string literal, which
    is how every one of them is loaded — and which is a lint, not proof: a
    step sharing its name with a protocol verb, the way ``keepalive`` does,
    is "used" by any assertion that mentions the verb. It also reads the
    manifest, so it notices a consumerless step and not a stepless consumer;
    the consumer's own ``KeyError`` covers that direction. This file is left
    out of the search so the exemptions above do not stand in for the
    consumers they excuse — at the price that its own loads do not count
    either, and every step it loads needs a consumer elsewhere.

    Args:
        scenario: Manifest key of the hand-recorded scenario to check.
    """
    here = Path(__file__)
    suite = "".join(
        path.read_text(encoding="utf-8")
        for path in here.parent.glob("*.py")
        if path != here
    )
    steps = {
        step["name"]: str(step["description"]) for step in load_json(scenario)["steps"]
    }
    unused = {name for name in steps if f'"{name}"' not in suite}
    assert unused == {name for scen, name in _DOCUMENTATION_ONLY if scen == scenario}
    for name in unused:
        assert "documentation" in steps[name], f"{name} does not say it is unused"


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


def test_every_typed_subsystem_has_a_field_to_land_in() -> None:
    """The event table and the snapshot are two lists nothing cross-checked.

    The test above pins the table against the capture; this pins it against
    `DeviceState`, which is where a parsed subsystem actually goes. An entry
    with no field of the same name reaches the caller as a bare
    `TypeError: DeviceState.__init__() got an unexpected keyword argument` —
    from `dataclasses.replace()`, on a healthy socket, and outside the
    `PiKVMError` hierarchy every other failure in this library stays inside
    (#143).
    """
    fields = {field.name for field in dataclasses.fields(DeviceState)}
    assert set(_STATE_MODELS) <= fields
    # `updated` names the event and `clients` is a bare count with no model,
    # so the two lists are not equal and are not meant to be.
    assert fields - set(_STATE_MODELS) == {"updated", "clients"}


def test_key_names_match_the_device_table() -> None:
    """The exported catalogue is kvmd's own table, name for name (#77).

    kvmd exposes it through no endpoint, so the fixture is the table read off
    the device itself and this is what keeps the copy in the library honest.
    """
    assert KEY_NAMES == set(load_json("hid_keys")["keys"])


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


def test_live_video_scenario_parses_into_its_models() -> None:
    """Every recorded live-video payload is one a model fully describes.

    The scenario is hand-recorded rather than captured, and it holds two
    shapes no other fixture does: ustreamer's own ``/state``, which is the
    object kvmd relays into ``StreamerState.streamer``, and what the media
    daemon says it can send — once over REST and once as the announcement
    the regular socket opens with.
    """
    steps = {entry["name"]: entry for entry in load_json("media_stream")["steps"]}

    for name in ("state_idle", "state_with_client"):
        state = Streamer.model_validate(steps[name]["response"]["result"])
        assert undeclared_fields(state) == [], name

    # A client of its own is the only thing that puts a row in clients_stat,
    # so without this the typed entries would go unproven.
    assert Streamer.model_validate(
        steps["state_with_client"]["response"]["result"]
    ).stream.clients_stat

    rest = MediaState.model_validate(
        steps["media_state"]["response"]["result"]["media"]
    )
    assert undeclared_fields(rest) == []

    announcement = steps["media_ws_regular"]["frames"][0]["msg"]
    assert announcement["event_type"] == "media"
    assert MediaState.model_validate(announcement["event"]) == rest
