"""The cross-references in docstrings must be ones the reference site can use.

Two things go wrong here, and they fail differently. A reStructuredText role
reaches the page as literal text and nothing complains, because mkdocstrings is
configured for Google style and no plugin ever looks at a role — the site built
cleanly for as long as all 124 of them were there. A mkdocstrings reference
whose target does not exist is caught: mkdocs-autorefs warns, and
``mkdocs build --strict`` fails on the warning.

So only the first of the two has no guard at all, and the second has one that
nothing runs — neither `.github/workflows/ci.yml` nor the deploy in
`docs.yml`, which calls ``mkdocs gh-deploy --force`` without ``--strict``. This
file covers the first and gives the second an answer in milliseconds, without a
docs build.

These are marker scans, not attempts to read prose. The documentation tests
removed in #107 were regular expressions trying to parse Python and failing at
it; a fixed token like ``:meth:`` needs no parsing, and the identifier check
imports the name rather than guessing at it. #107's own message names "reST
roles rendering literally" as one of the two defects its tests could not catch.

The identifier check proves a reference names a real object, not that the
reference site publishes an anchor for it — a page's ``members:`` list decides
that, and only a docs build can tell.
"""

import ast
import importlib
import pathlib
import re

import pydantic

import aiopikvm

SRC = pathlib.Path(aiopikvm.__file__).parent

REST_ROLE = re.compile(
    r":(?:py:)?(?:meth|class|attr|data|func|mod|exc|obj|const|ref|doc|term"
    r"|pymethod):`"
)
CROSS_REF = re.compile(r"\[[^\]]*\]\[([^\]]+)\]")

# ``Actions["#ComputerSystem.Reset"]["ResetType@Redfish.AllowableValues"]`` is
# two bracket pairs in a row inside a literal, and Markdown reads none of it as
# a link. Literals come out before the scan so that it does not either.
LITERAL = re.compile(r"``[^`]+``")


def _docstrings() -> list[tuple[str, int, str]]:
    """Every string-literal statement in the package, with where it came from.

    Returns:
        ``(path, line, text)`` for each docstring — module, class, function and
        attribute alike, since mkdocstrings renders all four.
    """
    found: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                rel = str(path.relative_to(SRC.parent))
                found.append((rel, node.lineno, node.value.value))
    return found


def _resolve(identifier: str) -> bool:
    """Whether *identifier* names something reachable from the package.

    A dotted path can cross from modules into objects at any point —
    ``aiopikvm.resources.hid.HIDResource.send_key`` is three modules and then
    two attributes — and a submodule is not an attribute of its parent until
    something imports it. So the longest importable prefix is found first, and
    only what is left of the path is walked from there.

    A pydantic field is not a class attribute in v2, so ``getattr`` cannot see
    one; ``model_fields`` is where they live, and a reference to a model's
    field is a reference to something real.

    Args:
        identifier: Dotted path from a cross-reference.

    Returns:
        Whether every step of the path exists.
    """
    if not identifier.startswith("aiopikvm"):
        return False
    parts = identifier.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj: object = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for part in parts[cut:]:
            if (
                isinstance(obj, type)
                and issubclass(obj, pydantic.BaseModel)
                and part in obj.model_fields
            ):
                obj = obj.model_fields[part]
                continue
            try:
                obj = getattr(obj, part)
            except AttributeError:
                return False
        return True
    return False


DOCSTRINGS = _docstrings()
MODULES = sorted(SRC.rglob("*.py"))


def test_every_module_contributed_a_docstring() -> None:
    """Guard the scans below: an empty corpus would satisfy them all."""
    assert len(DOCSTRINGS) >= len(MODULES)


def test_no_docstring_uses_a_reStructuredText_role() -> None:
    """A reST role renders as its own source text, and no build says so."""
    offenders = [
        f"{rel}:{line}" for rel, line, text in DOCSTRINGS if REST_ROLE.search(text)
    ]
    assert not offenders, (
        "reStructuredText roles render literally on the site; write them as "
        f"mkdocstrings cross-references instead: {offenders}"
    )


def test_every_cross_reference_names_a_real_object() -> None:
    """A reference to something that no longer exists renders as its source."""
    dangling = [
        f"{rel}:{line} -> {ref}"
        for rel, line, text in DOCSTRINGS
        for ref in CROSS_REF.findall(LITERAL.sub(" ", text))
        if not _resolve(ref)
    ]
    assert not dangling, (
        "these cross-references name nothing that exists, and reach the site "
        f"as their own source text: {dangling}"
    )


def test_the_package_still_carries_cross_references() -> None:
    """Guard the check above, which zero references would satisfy vacuously."""
    total = sum(
        len(CROSS_REF.findall(LITERAL.sub(" ", text))) for _, _, text in DOCSTRINGS
    )
    assert total >= len(MODULES)
