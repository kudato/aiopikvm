"""What the suite must not depend on: the machine it happens to run on.

`tests/conftest.py` takes the proxy environment away from every test outside
`tests/live`. These check that it reaches a test that asked for nothing, that
it catches every name `urllib` would read, and that what it leaves behind
holds up against a proxy the scrub never saw.
"""

import urllib.request

import pytest
from websockets.proxy import get_proxy
from websockets.uri import parse_uri

from tests.helpers import scrub_proxy_environment

PROXY = "http://127.0.0.1:3128"


def test_the_scrub_reaches_a_test_that_asked_for_nothing() -> None:
    """Nothing here requests the fixture, so it is autouse or it is absent.

    `getproxies()` is what *websockets* ends up consulting, so asserting on
    it rather than on the variables asks urllib's own scanner instead of
    repeating its rule. After the scrub the only thing it may report is the
    blanket bypass: anything else is a variable that survived, or the
    machine's own settings coming through.
    """
    assert urllib.request.getproxies() == {"no": "*"}


@pytest.mark.parametrize(
    "name",
    [
        "HTTPS_PROXY",
        "https_proxy",
        "Https_Proxy",
        "http_proxy",
        "ws_proxy",
        "wss_proxy",
        "socks_proxy",
        "all_proxy",
        "ALL_PROXY",
        "ftp_proxy",
    ],
)
def test_the_scrub_takes_every_name_urllib_would_read(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case and scheme both, because `getproxies_environment()` takes both.

    It case-folds the name and keeps anything ending in ``_proxy``, whatever
    scheme precedes it. A scrub that matched only the spellings some library
    happens to read today would leave the rest of them on the machine.
    """
    monkeypatch.setenv(name, PROXY)
    scrub_proxy_environment(monkeypatch)
    assert urllib.request.getproxies() == {"no": "*"}


@pytest.mark.parametrize("uri", ["ws://127.0.0.1:8000/", "wss://127.0.0.1:8000/"])
def test_a_proxy_appearing_later_is_bypassed_anyway(
    uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Why the scrub sets ``no_proxy`` rather than only deleting.

    On macOS and Windows `urllib.request.getproxies()` falls back to the
    machine's own settings when the environment names no proxy, so deleting
    alone would uncover a system-wide proxy instead of hiding one. A variable
    appearing after the scrub has run leaves `urllib` in the same place and is
    something a test can arrange, which the OS settings are not.

    All three variables go in because *websockets* consults a different list
    per scheme: ``http_proxy`` counts for an insecure URI only, ``wss_proxy``
    for a secure one only, ``https_proxy`` for either.
    """
    for name in ("http_proxy", "wss_proxy", "https_proxy"):
        monkeypatch.setenv(name, PROXY)
    assert get_proxy(parse_uri(uri)) is None
