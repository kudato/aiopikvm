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
REFERENCE_PAGE = "docs/reference/exceptions.md"
DIAGRAMS = (ERRORS_PAGE, REFERENCE_PAGE)


def page_text(page: str) -> str:
    """Read a documentation page.

    Args:
        page: Repository-relative path of the page.

    Returns:
        The page's text.
    """
    return (Path(__file__).parents[1] / page).read_text(encoding="utf-8")


def diagram_of(page: str) -> str:
    """Return a page's hierarchy diagram — its first fenced block.

    Args:
        page: Repository-relative path of the page holding the diagram.

    Returns:
        The text between the first pair of fences.
    """
    return page_text(page).partition("```")[2].partition("```")[0]


def drawn_tree(diagram: str) -> tuple[list[str], set[tuple[str, str]]]:
    """Parse a box-drawing tree into its names and its edges.

    Depth is what the indentation says: a branch glyph at column 0 is a child
    of the root, at column 4 a grandchild, and so on. A glyph off that grid
    is refused rather than rounded, because rounding is a second reading — a
    glyph at column 3 parses as a sibling while a reader sees a child, which
    is the drift this parser exists to catch.

    Args:
        diagram: The fenced block's text.

    Returns:
        Every name top-down as drawn, and a ``(child, parent)`` edge per
        drawn attachment.

    Raises:
        AssertionError: A branch glyph sits off the 4-column grid.
    """
    names: list[str] = []
    edges: set[tuple[str, str]] = set()
    stack: list[str] = []
    for line in diagram.splitlines():
        if not line.strip():
            continue
        name = line.split()[-1]
        marker = max(line.find("├"), line.find("└"))
        if marker < 0:
            depth = 0
        else:
            depth, off_grid = divmod(marker, 4)
            assert not off_grid, f"branch glyph off the 4-column grid: {line!r}"
            depth += 1
        stack[depth:] = [name]
        names.append(name)
        if depth:
            edges.add((name, stack[depth - 1]))
    return names, edges


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


def real_edges() -> set[tuple[str, str]]:
    """Return the ``(child, parent)`` edges of the exported hierarchy.

    Returns:
        One edge per exported exception below the root, taken from the
        class's own base.
    """
    return {
        (name, getattr(aiopikvm, name).__bases__[0].__name__)
        for name in exported_exceptions()
        if name != "PiKVMError"
    }


@pytest.mark.parametrize("page", DIAGRAMS)
def test_the_hierarchy_diagram_draws_the_real_tree(page: str) -> None:
    """The diagram is the class tree, not only the class list (#150).

    `WebRTCError` shipped exported while both diagrams ended at
    `WebSocketError`, so an `except` ladder built from either page picked the
    nearest name in it — which does not catch it, the two being siblings.
    Checking the drawn names alone would let the next drift redraw one
    *under* the other and restore that belief in a stronger form, so what is
    compared is the parent-child edges the indentation encodes.

    Args:
        page: Repository-relative path of the page holding the diagram.
    """
    names, edges = drawn_tree(diagram_of(page))
    assert set(names) == exported_exceptions()
    assert len(names) == len(set(names))
    assert edges == real_edges()


def test_the_error_table_lists_exactly_the_exported_exceptions() -> None:
    """The table beside the diagram is the other half of the same promise.

    Parsed out of the section's rows rather than searched for as substrings,
    and equal in both directions: a name added to the package without a row
    fails, and so does a row for something the package does not export —
    which a mere "is the name mentioned" scan would let through.
    """
    section = page_text(ERRORS_PAGE).partition("## Exception types")[2]
    section = section.partition("\n## ")[0]
    rows = [
        line.split("`")[1] for line in section.splitlines() if line.startswith("| `")
    ]
    assert set(rows) == exported_exceptions()
    assert len(rows) == len(set(rows))


def test_the_reference_page_renders_every_exception_in_diagram_order() -> None:
    """Each drawn class gets a `:::` block, in the order the diagram walks.

    The page contradicted itself once — `WebRTCError` rendered in the body
    below a diagram that left it out — and the blocks have always followed
    the diagram top-down, so the missing-block drift and the out-of-order
    drift are one comparison (#150).
    """
    blocks = [
        line.split("aiopikvm.")[1].strip()
        for line in page_text(REFERENCE_PAGE).splitlines()
        if line.startswith("::: aiopikvm.")
    ]
    names, _ = drawn_tree(diagram_of(REFERENCE_PAGE))
    assert blocks == names
