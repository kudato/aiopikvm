"""What `import aiopikvm` puts within reach.

`__all__` is the promise; these tests are what keeps it from drifting from
the modules the names actually live in.
"""

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
