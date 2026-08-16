"""AuthResource tests.

Every mocked response is a step of the ``auth_roundtrip`` scenario, recorded
against kvmd 4.186. The request side matters as much as the response here:
kvmd reads ``/auth/login`` with a form parser and identifies the session to
drop by cookie, and getting either wrong fails on a real device while a
mock happily accepts it.
"""

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from aiopikvm import AuthError, ConfigurationError, ConnectError, PiKVM
from tests.fixtures import load_json

TOKEN = "f" * 64
"""Stand-in for kvmd's token: 64 hex characters, as valid_auth_token demands."""


def step(name: str) -> dict[str, Any]:
    """Return one recorded step of the auth round trip.

    Args:
        name: Step name in ``auth_roundtrip.json``.

    Returns:
        The step entry, including its status and response body.

    Raises:
        KeyError: If the scenario has no such step.
    """
    for entry in load_json("auth_roundtrip")["steps"]:
        if entry["name"] == name:
            return dict(entry)
    raise KeyError(f"No step {name!r} in the auth_roundtrip scenario")


def replay(name: str, *, cookie: str | None = None) -> httpx.Response:
    """Build the response kvmd gave at a recorded step.

    Args:
        name: Step name in ``auth_roundtrip.json``.
        cookie: Session token to hand out, for the steps that set one.

    Returns:
        The response, with the ``auth_token`` cookie when the step sets it.
    """
    entry = step(name)
    response = httpx.Response(entry["status"], json=entry["body"])
    if cookie is not None:
        response.headers["set-cookie"] = (
            f"auth_token={cookie}; HttpOnly; Path=/; SameSite=Strict"
        )
    return response


def form(request: httpx.Request) -> dict[str, list[str]]:
    """Parse a form-encoded request body."""
    return parse_qs(request.content.decode())


# --- login ------------------------------------------------------------


async def test_login_sends_a_form_body(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """kvmd parses login with aiohttp's form parser, so JSON arrives empty."""
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    await client.auth.login("admin", "secret")

    request = mock_api.calls[-1].request
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert form(request) == {"user": ["admin"], "passwd": ["secret"], "expire": ["0"]}


async def test_login_returns_the_token_from_the_cookie(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The token exists only in the Set-Cookie header, never in the body."""
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    assert await client.auth.login("admin", "secret") == TOKEN
    assert client.cookies.get("auth_token") == TOKEN


async def test_login_returns_empty_string_when_kvmd_hands_out_no_session(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """With auth disabled kvmd answers 200 and sets no cookie."""
    mock_api.post("/api/auth/login").mock(return_value=replay("login_form_body"))
    assert await client.auth.login("admin", "secret") == ""


async def test_login_appends_totp_to_the_password(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    await client.auth.login("admin", "pass", totp="123456")
    assert form(mock_api.calls[-1].request)["passwd"] == ["pass123456"]


async def test_login_sends_expire(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    await client.auth.login("admin", "secret", expire=60)
    assert form(mock_api.calls[-1].request)["expire"] == ["60"]


async def test_login_rejects_a_negative_expire(client: PiKVM) -> None:
    """Caught here so the only 400 login can produce is about credentials."""
    with pytest.raises(ConfigurationError):
        await client.auth.login("admin", "secret", expire=-1)


async def test_login_with_a_wrong_password_raises_auth_error(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/auth/login").mock(return_value=replay("login_wrong_passwd"))
    with pytest.raises(AuthError) as exc_info:
        await client.auth.login("admin", "wrong")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error == "ForbiddenError"


async def test_login_with_a_rejected_user_name_raises_auth_error(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """kvmd reports a name its regex refuses as a plain 400 ValidatorError."""
    mock_api.post("/api/auth/login").mock(return_value=replay("login_invalid_user"))
    with pytest.raises(AuthError) as exc_info:
        await client.auth.login("ADMIN!", "secret")
    assert exc_info.value.status_code == 400
    assert exc_info.value.error == "ValidatorError"


# --- check ------------------------------------------------------------


async def test_check_returns_nothing(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/auth/check").mock(return_value=replay("check_with_cookie"))
    assert await client.auth.check() is None


async def test_check_without_credentials_raises(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/auth/check").mock(
        return_value=replay("check_without_credentials")
    )
    with pytest.raises(AuthError) as exc_info:
        await client.auth.check()
    assert exc_info.value.status_code == 401
    assert exc_info.value.error == "UnauthorizedError"


async def test_check_with_a_dropped_session_raises(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/auth/check").mock(return_value=replay("check_after_logout"))
    with pytest.raises(AuthError) as exc_info:
        await client.auth.check()
    assert exc_info.value.status_code == 403


# --- logout -----------------------------------------------------------


async def test_logout_sends_the_stored_cookie(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The X-KVMD headers get the call through auth; the cookie names the
    session. Without it kvmd answers 400."""
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    mock_api.post("/api/auth/logout").mock(return_value=replay("logout"))

    await client.auth.login("admin", "secret")
    await client.auth.logout()

    assert f"auth_token={TOKEN}" in mock_api.calls[-1].request.headers["cookie"]


async def test_logout_accepts_an_explicit_token(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/auth/logout").mock(return_value=replay("logout"))
    await client.auth.logout(TOKEN)
    assert f"auth_token={TOKEN}" in mock_api.calls[-1].request.headers["cookie"]


async def test_logout_drops_the_cookie(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/auth/logout").mock(return_value=replay("logout"))
    await client.auth.logout(TOKEN)
    assert client.cookies.get("auth_token") is None


async def test_logout_without_a_session_sends_nothing(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Rather than let kvmd answer 400 for a request that cannot succeed."""
    with pytest.raises(ConfigurationError):
        await client.auth.logout()
    assert not mock_api.calls


async def test_logout_drops_a_cookie_kvmd_refuses(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A token kvmd does not know is dead; keeping it only breaks later calls."""
    mock_api.post("/api/auth/logout").mock(return_value=replay("check_after_logout"))
    with pytest.raises(AuthError):
        await client.auth.logout(TOKEN)
    assert client.cookies.get("auth_token") is None


async def test_logout_keeps_the_cookie_when_the_connection_fails(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The session may well still be alive, so the token stays usable."""
    mock_api.post("/api/auth/logout").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ConnectError):
        await client.auth.logout(TOKEN)
    assert client.cookies.get("auth_token") == TOKEN
