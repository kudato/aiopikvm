"""Contract tests — the models must handle what a real kvmd actually sends.

Payloads come from ``tests/fixtures/data``, captured from a live PiKVM running
kvmd 4.186. A case the library cannot handle today carries a strict ``xfail``
naming the issue that tracks it: the suite stays green while the gap is known,
and the moment a fix lands the stale marker fails instead of quietly hiding the
fact that the contract is now satisfied.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, NamedTuple, TypeAliasType, get_args, get_origin

import pytest
from pydantic import BaseModel

import aiopikvm
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


def _where(path: Path) -> str:
    """Return *path* the way a failure message should name it.

    Relative to the repository where it is inside it, absolute where it is
    not. The package is reached through its own ``__path__``, so a
    non-editable install puts it under ``site-packages``, and
    ``relative_to`` answers a path outside its argument with ``ValueError``
    rather than with a name.

    Args:
        path: File one of the scans below read a value out of.

    Returns:
        The path to print, relative where that is possible.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _module_files() -> list[Path]:
    """Return every source file of the package.

    Located through the package's own ``__path__`` rather than by guessing
    at ``src``, which would go on matching an unrelated file after a move.

    Returns:
        Every ``.py`` file in the package, sorted.

    Raises:
        AssertionError: If the walk comes back empty. A package that is not
            where it says it is looks like nothing at all from here, and a
            scan of nothing goes on passing.
    """
    files = sorted(Path(aiopikvm.__path__[0]).rglob("*.py"))
    assert files, "no module found; the package is not where it says it is"
    return files


def _prose() -> list[Path]:
    """Return the prose an example can be copied out of.

    Three corpora: the docs tree, the README, and every module of the
    package. The modules are in because a page under ``docs/reference`` is
    little more than a ``:::`` directive — the prose a reader of the API
    reference copies from is the docstrings mkdocstrings renders out of the
    source, so an example written in one is as published as one written in
    a guide.

    Reading the code alongside the docstrings is the price, and cheap for
    one reason only: every value below is matched inside a call. The
    docstrings do spell vocabularies out in prose, refused values included —
    ``GracefulRestart`` and ``"USB"`` are both in there — so a pattern
    widened to bare quotes would fail on them.

    Two corpora are deliberately out, and both would fail today. ``tests/``
    and ``CHANGELOG.md`` put values the device refuses *into calls* on
    purpose — ``reset("aiopikvm-does-not-exist")``, a key name that falls
    out empty — so scanning them would fail on the examples that are correct
    precisely because they are wrong.

    Returns:
        Every file the scans below read, guides first.

    Raises:
        AssertionError: If the docs tree comes back empty. A renamed one
            looks like nothing at all from here.
    """
    guides = sorted(ROOT.joinpath("docs").rglob("*.md"))
    assert guides, "no guide found; the docs tree moved"
    return [*guides, ROOT / "README.md", *_module_files()]


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


_QUOTED = r"""(?P<quote>["'])(?P<text>[^"'\n]*)(?P=quote)"""
"""One string literal, in either of the quotes Python spells one with.

``ruff format`` normalises the code to double quotes, but it does not touch
a fenced block in a Markdown guide, so a single-quoted example is a
published example — and a pattern that only knew the double quote would let
one carry a value the parameter refuses, silently. The closing quote is a
backreference so that an apostrophe inside a double-quoted string cannot
end it.
"""

_ARGS = r"(?P<args>(?:[^()]|\([^()]*\))*)"
"""The argument list of a call, one level of nesting included.

For the calls whose vocabulary can appear in more than one argument — the
key names are variadic, and the ATX pair take the name last. A plain
``[^)]*`` stops at the first ``)`` it meets, so a nested call anywhere in
the list hides every argument after it: the scan then reads the ones before
it, passes, and never reports that it stopped early.
"""


def _scan(pattern: str, path: Path) -> list[str]:
    """Return every value *pattern* reads out of *path*.

    Two shapes of pattern, told apart by the group they capture. One with a
    ``text`` group has found the value itself. One with an ``args`` group has
    found a whole argument list instead, and every string literal in it is a
    value — which holds only where no other argument of that call is a
    string, and is why ``reset()`` is matched the other way: its second
    argument is a system id.

    Args:
        pattern: Pattern with a ``text`` group or an ``args`` group.
        path: File to read.

    Returns:
        Each value found, in the order the file spells them.
    """
    found: list[str] = []
    for match in re.finditer(pattern, path.read_text(encoding="utf-8")):
        if "args" in match.groupdict():
            found += [inner["text"] for inner in re.finditer(_QUOTED, match["args"])]
        else:
            found.append(match["text"])
    return found


_KEY_CALLS = rf"(?<!def )send_(?:key|shortcut)\({_ARGS}\)"


def test_documented_key_names_are_ones_kvmd_accepts() -> None:
    """No published example types a key that does not exist.

    A wrong name in an example fails with HTTP 400 over HTTP and with nothing
    at all over the WebSocket, which is exactly the failure this catalogue
    exists to prevent (#77).

    The lookbehind skips the definitions: a parameter of ``send_key()`` that
    ever takes a string default is not a key name, and reading it as one
    would fail the suite for a correct change.
    """
    found: dict[str, set[str]] = {}
    for path in _prose():
        for name in _scan(_KEY_CALLS, path):
            found.setdefault(name, set()).add(_where(path))
    assert found, "no key name found at all; the pattern stopped matching"
    # Which file, as in the scan below: 115 names over three corpora leave
    # a reader nothing to grep for otherwise.
    assert set(found) <= KEY_NAMES, sorted(
        (name, sorted(found[name])) for name in set(found) - KEY_NAMES
    )


def _values(annotation: Any, seen: frozenset[Any] = frozenset()) -> tuple[Any, ...]:
    """Return every literal value reachable inside *annotation*.

    Empty for a type built out of no literals at all, which is how an
    ordinary alias — ``type Params = dict[str, Any]`` — is told apart from a
    vocabulary below. Recursive, so that an alias for an alias, a union of
    literals and a ``Literal[...] | None`` all read as the vocabulary they
    are: a shape this stopped seeing would stop being documented, silently,
    which is the one thing the checks below exist to prevent. A member that
    is an alias in its own right counts too — ``Literal[Inner, "ps2"]`` is
    legal, and ``str()`` of that first member is the string ``"Inner"``,
    a value nothing in the library accepts and nothing here would question.

    *seen* stops an alias that refers to itself — ``type Json = str | int |
    list[Json]`` is the ordinary shape of one — from recursing forever.
    Without it that alias is not a failing test but a ``RecursionError``
    during collection, which takes the whole session down with it.

    An alias whose value will not evaluate reads as no vocabulary at all,
    for the same reason. ``type Ports = dict[str, ATXResource]`` in a module
    that imports ``ATXResource`` under ``TYPE_CHECKING`` — the shape the
    style guide asks for — raises ``NameError`` on the lazy ``__value__``,
    and that too is a collection error rather than one red test.

    Members come back as the type spells them, not as text: the check that
    a vocabulary is made of strings at all has to be able to see one that is
    not. :func:`_texts` is what every comparison against prose reads.

    Args:
        annotation: Type to read, usually a ``type`` alias.
        seen: Aliases already being read further up the recursion.

    Returns:
        Each literal value, in the order the type spells them.
    """
    if isinstance(annotation, TypeAliasType):
        if annotation in seen:
            return ()
        try:
            value = annotation.__value__
        except NameError:
            return ()
        return _values(value, seen | {annotation})
    if get_origin(annotation) is Literal:
        return tuple(
            member
            for arg in get_args(annotation)
            for member in (
                _values(arg, seen) if isinstance(arg, TypeAliasType) else (arg,)
            )
        )
    return tuple(value for arg in get_args(annotation) for value in _values(arg, seen))


def _texts(annotation: Any) -> tuple[str, ...]:
    """Return :func:`_values` as the text every comparison here is against.

    A regex capture out of a guide and a cell of its table are both text, so
    this is the side the prose is held to.

    Args:
        annotation: Type to read, usually a ``type`` alias.

    Returns:
        Each literal value as written down.
    """
    return tuple(str(value) for value in _values(annotation))


_TYPED_VALUES = (
    ("keyboard_output", rf"keyboard_output={_QUOTED}", KeyboardOutput),
    ("mouse_output", rf"mouse_output={_QUOTED}", MouseOutput),
    # The keyword form as well as the positional one: both parameters are
    # ordinary positional-or-keyword, so an example is free to name them,
    # and a pattern that only knew the position would skip such a call
    # without a word.
    ("mouse button", rf"send_mouse_button\(\s*(?:button=)?{_QUOTED}", MouseButton),
    ("compression", rf"compress={_QUOTED}", Compression),
    # The ATX pair take the name last, so the whole argument list is matched
    # and every string in it read. Nothing else either of them takes is a
    # string — the port is a number — so there is nothing else to pick up.
    ("ATX action", rf"atx_power\({_ARGS}\)", ATXAction),
    ("ATX button", rf"atx_click\({_ARGS}\)", ATXButton),
    # Not the argument list here: ``reset()`` takes a system id second, and
    # ``reset("ForceOff", "SwitchPort0")`` is in the guide, so only the
    # first argument is a reset type.
    #
    # Four other resources have a ``reset()``, and not one of them can put a
    # string where this looks: ``switch.reset`` takes a unit number,
    # ``streamer.reset`` a keyword-only timeout, ``hid.reset`` and
    # ``msd.reset`` nothing at all. That is what lets the pattern go
    # unqualified — and going unqualified is what buys the two examples that
    # name the type without naming the resource, ``ForceRestart`` among them:
    # the default, and the value the guide's danger block is about. The
    # lookbehind is the one boundary that has to be spelled out, or
    # ``click_reset("...")`` matches. The guide spells refused types out too,
    # ``GracefulRestart`` and ``"forceoff"``, and neither is inside a call.
    ("reset type", rf"(?<!\w)reset\(\s*(?:reset_type=)?{_QUOTED}", ResetType),
)
_TYPE_TABLE = ROOT / "docs" / "guide" / "error-handling.md"
_TYPE_HEADING = "## Values the type checker catches"


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
    found: dict[str, set[str]] = {}
    for path in _prose():
        for value in _scan(pattern, path):
            found.setdefault(value, set()).add(_where(path))
    assert found, f"no {what} found at all; the pattern stopped matching"
    allowed = set(_texts(alias))
    # Which file, as well as which value: the pattern reads the docs tree,
    # the README and every module, and a bare value leaves a reader
    # grepping for it. The path is relative to the repository because a
    # basename says too little across three corpora — sixteen of them repeat,
    # ``hid.md`` and ``hid.py`` and ``__init__.py`` among them.
    assert set(found) <= allowed, sorted(
        (value, sorted(found[value])) for value in set(found) - allowed
    )


def _modules() -> list[ModuleType]:
    """Return every module in the package, the top-level one included.

    Named off the files rather than discovered with
    ``pkgutil.walk_packages``, which skips a directory that has no
    ``__init__.py`` — and skips it silently, yielding not so much as the
    directory itself. Such a directory is a PEP 420 namespace portion and
    its modules import perfectly well, so a vocabulary in one would be
    invisible here, and invisible here means no row and no scanner is ever
    asked for it.

    Returns:
        Every module of the package, the top-level one first.
    """
    root = Path(aiopikvm.__path__[0])
    names = ["aiopikvm"]
    for path in _module_files():
        parts = path.relative_to(root).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            names.append(".".join(("aiopikvm", *parts)))
    return [importlib.import_module(name) for name in names]


def _vocabularies() -> dict[str, TypeAliasType]:
    """Return every literal type the library defines, by name.

    Read out of the modules, and out of every module in the package rather
    than a list of the interesting ones: a hand-written inventory catches
    only a type somebody remembered to add to *it*, and a hand-written list
    of modules stops covering one that gets renamed, split or moved.

    Only aliases with literal values count, as :func:`_values` reads them.
    The style guide asks for ``type`` aliases generally, so an ordinary one
    — ``type Params = dict[str, Any]`` — will turn up in a resource module
    sooner or later, and it has no vocabulary for the guide to spell out.

    Returns:
        Every vocabulary type, keyed by the name the guide's table uses.

    Raises:
        AssertionError: If two modules define one name, or if the scan
            comes back empty.
    """
    aliases: dict[str, TypeAliasType] = {}
    for module in _modules():
        for name, value in vars(module).items():
            if (
                name.startswith("_")
                or not isinstance(value, TypeAliasType)
                or value.__module__ != module.__name__
                or not _values(value)
            ):
                continue
            # Keyed by the bare name, because that is what the guide's
            # table names. Two modules defining one name would leave the
            # second checked against the first one's row.
            assert name not in aliases, f"two modules define {name}"
            aliases[name] = value
    # Everything below compares this against the docs, so an empty side
    # would agree with an empty table and prove nothing at all.
    assert aliases, "no vocabulary type found at all; the scan stopped working"
    return aliases


def _type_table() -> dict[str, list[str]]:
    """Parse the guide's table of typed values into ``{type: [value, ...]}``.

    The table is where a reader goes to find out what a parameter takes, and
    the scan above cannot see it, since nothing in it is shaped like a call.
    It is not the only prose that spells a vocabulary out — six of the seven
    alias docstrings do too — but it is the one the guides link to, so it is
    the one held to the type.

    Every row of that one table is read, and no row is matched against the
    library on the way in: a row naming a type that does not exist has to
    reach the comparison to be caught by it.

    Returns:
        Each documented type, with the values its row lists, in order.

    Raises:
        AssertionError: If the section or its table is gone, or if one type
            has two rows.
    """
    _, _, rest = _TYPE_TABLE.read_text(encoding="utf-8").partition(_TYPE_HEADING)
    assert rest, f"{_TYPE_TABLE.name} no longer has a {_TYPE_HEADING!r} section"
    rows: dict[str, list[str]] = {}
    for line in rest.split("\n## ")[0].splitlines():
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 3 or not re.fullmatch(r"`\w+`", cells[1]):
            continue
        name = cells[1].strip("`")
        assert name not in rows, f"the guide has two rows for {name}"
        rows[name] = [
            # The empty compression mode is spelled `""` in the table.
            "" if value == '""' else value
            for value in re.findall(r"`([^`]*)`", cells[2])
        ]
    assert rows, f"the {_TYPE_HEADING!r} section no longer holds a table"
    return rows


def test_the_guide_lists_every_typed_vocabulary() -> None:
    """A type nobody documented is one nobody knows to use (#68).

    The library half is read off the source rather than listed here: add a
    vocabulary to a resource module and this fails until the guide's table
    has a row for it.
    """
    documented = set(_type_table())
    defined = set(_vocabularies())
    assert documented == defined, (
        f"no row: {sorted(defined - documented)}; "
        f"no such type: {sorted(documented - defined)}"
    )


def test_every_vocabulary_has_a_docs_example_scan() -> None:
    """And a type nobody scans for is one the guides can misspell (#68).

    ``_TYPED_VALUES`` is the one hand-written list of aliases left, and the
    whole apparatus exists because a hand-written list catches only what
    somebody remembered to add to it: ``ResetType`` shipped without a
    scanner for exactly that reason. This is what makes the omission
    impossible rather than merely unlikely.
    """
    scanned = {alias for _, _, alias in _TYPED_VALUES}
    defined = set(_vocabularies().values())
    assert scanned == defined, (
        f"no scanner: {sorted(a.__name__ for a in defined - scanned)}; "
        f"no vocabulary: {sorted(a.__name__ for a in scanned - defined)}"
    )


@pytest.mark.parametrize("name", sorted(_vocabularies()))
def test_the_guide_spells_a_vocabulary_out_in_full(name: str) -> None:
    """Each row of that table is its type's values, all of them (#68).

    Equality, not containment: a value the table invents misleads as badly
    as one it leaves out, and both are invisible to a type checker, which
    reads the library and not the prose.
    """
    # The type is read first, and checked first. A vocabulary whose members
    # read alike once written down — ``Literal[1, "1"]`` — cannot be spelled
    # out at all: the honest row trips the duplicate check below it and the
    # incomplete one would pass the equality. Behind the row lookup, that
    # never got as far as saying so, since a type nobody has documented yet
    # is the order anybody adding one works in.
    members = _values(_vocabularies()[name])
    # Both sides of every comparison here are text, so a member that is not
    # a string is compared as ``str()`` of itself: ``Literal[1]`` would
    # accept the ``"1"`` of an example that cannot legally pass it. Nothing
    # kvmd takes in a query string is anything but a string, and this is
    # what says so rather than assuming it.
    odd = [member for member in members if not isinstance(member, str)]
    assert not odd, f"{name} has values no example or table cell can hold: {odd}"
    values = tuple(str(member) for member in members)
    assert len(values) == len(set(values)), f"{name} has two values that read alike"
    rows = _type_table()
    assert name in rows, f"the guide's table has no row for {name}"
    listed = rows[name]
    assert len(listed) == len(set(listed)), f"the guide repeats a value: {listed}"
    assert set(listed) == set(values), (
        f"the guide invents: {sorted(set(listed) - set(values))}; "
        f"the guide omits: {sorted(set(values) - set(listed))}"
    )


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
    assert set(available) <= set(_texts(MouseOutput))


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
