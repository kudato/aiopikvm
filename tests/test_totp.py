"""TOTP generation, checked against RFC 6238's own test vectors.

Writing the algorithm out rather than taking a dependency is only defensible
if it is held to the published numbers, so that is what happens here: the
vectors in RFC 6238 Appendix B, verbatim.

kvmd's parameters are fixed by what it runs — `pyotp.TOTP(secret)` with the
defaults, HMAC-SHA1 over six digits every thirty seconds — and those vectors
are eight digits, so the eight-digit cases pin the algorithm and a separate
case pins the truncation to six.
"""

import base64
import time

import httpx
import pytest
import respx

from aiopikvm import TOTP, ConfigurationError, PiKVM

RFC_SEED = base64.b32encode(b"12345678901234567890").decode("ascii")
"""RFC 6238's SHA1 seed, base32 as this class takes it."""

RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize(("timestamp", "code"), RFC_VECTORS)
def test_matches_the_rfc_6238_vectors(timestamp: int, code: str) -> None:
    assert TOTP(RFC_SEED, digits=8).at(timestamp) == code


def test_six_digits_are_the_low_end_of_the_same_number() -> None:
    # kvmd reads passwd[-6:], so six is what matters; the RFC prints eight.
    assert TOTP(RFC_SEED).at(59) == "287082"


def test_the_code_holds_for_one_interval_and_then_changes() -> None:
    # Steps are counted from the epoch, not from the first call, so the
    # window has to be entered at its own boundary to see it whole.
    totp = TOTP(RFC_SEED)
    start = 1111111109 - (1111111109 % 30)
    assert totp.at(start) == totp.at(start + 29)
    assert totp.at(start) != totp.at(start + 30)


def test_the_secret_is_read_the_way_a_person_would_copy_it() -> None:
    spaced = " ".join(RFC_SEED[i : i + 4] for i in range(0, len(RFC_SEED), 4))
    assert TOTP(spaced.lower()).at(59) == TOTP(RFC_SEED).at(59)


def test_an_unpadded_secret_is_accepted() -> None:
    # pyotp.random_base32() returns 32 characters with no padding, which is
    # exactly what kvmd writes to /etc/kvmd/totp.secret.
    unpadded = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    assert TOTP(unpadded).at(59) == TOTP(RFC_SEED).at(59)


def test_calling_it_uses_the_current_time() -> None:
    totp = TOTP(RFC_SEED)
    assert totp() == totp.at(time.time())


@pytest.mark.parametrize(
    ("secret", "match"),
    [
        ("", "empty"),
        ("not base32 at all!", "not base32"),
        ("1", "not base32"),
    ],
)
def test_an_unusable_secret_is_reported_as_configuration(
    secret: str, match: str
) -> None:
    with pytest.raises(ConfigurationError, match=match):
        TOTP(secret)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"digits": 0}, "digits must be positive"),
        ({"interval": 0}, "interval must be positive"),
    ],
)
def test_unusable_parameters_are_refused(kwargs: dict[str, int], match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        TOTP(RFC_SEED, **kwargs)


async def test_the_client_asks_for_a_code_per_request(
    mock_api: respx.MockRouter,
) -> None:
    # The bug this fixes: PiKVM(totp="123456") froze the string into the
    # client's headers, so a client outliving one 30-second window went on
    # authenticating with a code that had stopped being valid.
    codes = iter(["111111", "222222"])
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    async with PiKVM(
        "https://pikvm.local", passwd="secret", totp=lambda: next(codes)
    ) as kvm:
        await kvm.request("GET", "/api/atx")
        await kvm.request("GET", "/api/atx")
    sent = [call.request.headers["X-KVMD-Passwd"] for call in mock_api.calls]
    assert sent == ["secret111111", "secret222222"]


async def test_a_literal_code_is_still_sent_as_given(
    mock_api: respx.MockRouter,
) -> None:
    # Unchanged for anyone passing the code they just read off a phone; it
    # is simply good for the window it belongs to and no longer.
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    async with PiKVM("https://pikvm.local", passwd="secret", totp="123456") as kvm:
        await kvm.request("GET", "/api/atx")
        await kvm.request("GET", "/api/atx")
    sent = {call.request.headers["X-KVMD-Passwd"] for call in mock_api.calls}
    assert sent == {"secret123456"}


async def test_a_totp_object_reaches_the_wire(mock_api: respx.MockRouter) -> None:
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    totp = TOTP(RFC_SEED)
    async with PiKVM("https://pikvm.local", passwd="secret", totp=totp) as kvm:
        await kvm.request("GET", "/api/atx")
    sent = mock_api.calls[-1].request.headers["X-KVMD-Passwd"]
    assert sent.startswith("secret")
    assert sent[len("secret") :] == totp()
