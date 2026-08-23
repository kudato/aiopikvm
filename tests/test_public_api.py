"""What `import aiopikvm` puts within reach.

`__all__` is the promise; these tests are what keeps it from drifting from
the modules the names actually live in.
"""

from pathlib import Path

import pytest

import aiopikvm
from aiopikvm.resources import hid, redfish, system

# Every vocabulary kvmd's API is typed with, and where each one is defined.
# A name in a signature the package exports has to be reachable from the same
# place that signature is, or a typed wrapper needs a second, deeper import
# for no reason a caller can see.
VOCABULARIES = {
    "KEY_NAMES": hid.KEY_NAMES,
    "KeyboardOutput": hid.KeyboardOutput,
    "MouseButton": hid.MouseButton,
    "MouseOutput": hid.MouseOutput,
    "RESET_TYPES": redfish.RESET_TYPES,
    "ResetType": redfish.ResetType,
    "InfoField": system.InfoField,
}


def test_everything_declared_is_reachable() -> None:
    missing = [name for name in aiopikvm.__all__ if not hasattr(aiopikvm, name)]
    assert missing == []


def test_nothing_is_declared_twice() -> None:
    assert len(set(aiopikvm.__all__)) == len(aiopikvm.__all__)


def test_the_vocabularies_are_exported() -> None:
    assert set(VOCABULARIES) <= set(aiopikvm.__all__)


def test_the_exported_vocabularies_are_the_defining_objects() -> None:
    """Re-exported, not redefined: one of each, or the two drift apart."""
    for name, defined in VOCABULARIES.items():
        assert getattr(aiopikvm, name) is defined


ERRORS_PAGE = "docs/guide/error-handling.md"
DIAGRAMS = (ERRORS_PAGE, "docs/reference/exceptions.md")


def exported_exceptions() -> set[str]:
    """Return every exception `import aiopikvm` puts within reach.

    Returns:
        The names in ``__all__`` that are `PiKVMError` or descend from it.
    """
    return {
        name
        for name in aiopikvm.__all__
        if isinstance(value := getattr(aiopikvm, name), type)
        and issubclass(value, aiopikvm.PiKVMError)
    }


@pytest.mark.parametrize("page", DIAGRAMS)
def test_every_exported_exception_is_in_the_hierarchy_diagram(page: str) -> None:
    """A diagram that presents itself as complete has to be (#150).

    `WebRTCError` shipped exported, and the WebRTC guide told readers to catch
    it, while both diagrams still ended at `WebSocketError` — so an `except`
    ladder built from either page picked the nearest name in it, which does
    not catch it.

    Args:
        page: Repository-relative path of the page holding the diagram.
    """
    text = (Path(__file__).parents[1] / page).read_text(encoding="utf-8")
    diagram = text.partition("```")[2].partition("```")[0]
    drawn = {line.split()[-1] for line in diagram.splitlines() if line.strip()}
    assert drawn == exported_exceptions()


def test_every_exported_exception_has_a_row_in_the_error_table() -> None:
    """The table beside the diagram is the other half of the same promise."""
    text = (Path(__file__).parents[1] / ERRORS_PAGE).read_text(encoding="utf-8")
    missing = [name for name in exported_exceptions() if f"| `{name}` |" not in text]
    assert missing == []
