"""Which credential the client puts on the wire, per `auth` mode.

kvmd tries its four sources in a fixed order and the first one *present*
decides the request, so what matters here is as much what is **absent** as
what is sent: a client meaning to authenticate by session that still carries
`X-KVMD-User` never reaches the session check at all.
"""

import asyncio
import base64
import itertools
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from aiopikvm import AuthError, ConfigurationError, PiKVM

_COOKIE = "auth_token"
OK = {"ok": True, "result": {}}
TOKEN = "a" * 64
OTHER_TOKEN = "b" * 64

URL = "https://pikvm.local"


def _login_route(mock_api: respx.MockRouter, token: str = TOKEN) -> respx.Route:
    """Answer `/api/auth/login` with a session cookie, the way kvmd does."""
    return mock_api.post("/api/auth/login").mock(
        return_value=httpx.Response(
            200, json=OK, headers={"Set-Cookie": f"auth_token={token}; Path=/"}
        )
    )


async def test_headers_mode_sends_the_kvmd_headers(
    mock_api: respx.MockRouter,
) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret") as kvm:
        await kvm.request("GET", "/api/atx")
    request = mock_api.calls[-1].request
    assert request.headers["X-KVMD-User"] == "admin"
    assert request.headers["X-KVMD-Passwd"] == "secret"
    assert "Authorization" not in request.headers


async def test_headers_mode_appends_the_totp_code(
    mock_api: respx.MockRouter,
) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", totp="123456") as kvm:
        await kvm.request("GET", "/api/atx")
    assert mock_api.calls[-1].request.headers["X-KVMD-Passwd"] == "secret123456"


async def test_basic_mode_sends_authorization_and_nothing_else(
    mock_api: respx.MockRouter,
) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="basic") as kvm:
        await kvm.request("GET", "/api/atx")
    request = mock_api.calls[-1].request
    expected = base64.b64encode(b"admin:secret").decode("ascii")
    assert request.headers["Authorization"] == f"Basic {expected}"
    # kvmd checks the headers before Basic; sending both would mean the
    # Authorization header decided nothing.
    assert "X-KVMD-User" not in request.headers
    assert "X-KVMD-Passwd" not in request.headers


async def test_basic_mode_appends_the_totp_code(
    mock_api: respx.MockRouter,
) -> None:
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(
        URL, user="admin", passwd="secret", totp="123456", auth="basic"
    ) as kvm:
        await kvm.request("GET", "/api/atx")
    raw = base64.b64decode(mock_api.calls[-1].request.headers["Authorization"][6:])
    assert raw == b"admin:secret123456"


async def test_cookie_mode_logs_in_once_and_then_carries_the_token(
    mock_api: respx.MockRouter,
) -> None:
    login = _login_route(mock_api)
    atx = mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.request("GET", "/api/atx")
        await kvm.request("GET", "/api/atx")
    assert login.call_count == 1
    assert atx.call_count == 2
    for call in atx.calls:
        assert "X-KVMD-User" not in call.request.headers
        assert "Authorization" not in call.request.headers
        assert f"auth_token={TOKEN}" in call.request.headers["Cookie"]


async def test_cookie_mode_sends_the_password_only_to_the_login(
    mock_api: respx.MockRouter,
) -> None:
    login = _login_route(mock_api)
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.request("GET", "/api/atx")
    assert b"passwd=secret" in login.calls[0].request.content
    assert "secret" not in str(mock_api.calls[-1].request.headers)


async def test_cookie_mode_opens_a_new_session_when_the_token_is_refused(
    mock_api: respx.MockRouter,
) -> None:
    # A token outlives nothing in particular: kvmd expires it, or another
    # logout of the same user drops it. The call itself is still valid.
    tokens = iter([TOKEN, OTHER_TOKEN])
    login = mock_api.post("/api/auth/login").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=OK,
            headers={"Set-Cookie": f"auth_token={next(tokens)}; Path=/"},
        )
    )
    answers = iter([httpx.Response(403, json=OK), httpx.Response(200, json=OK)])
    atx = mock_api.get("/api/atx").mock(side_effect=lambda request: next(answers))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.request("GET", "/api/atx")
    assert login.call_count == 2
    assert atx.call_count == 2
    assert f"auth_token={OTHER_TOKEN}" in atx.calls[-1].request.headers["Cookie"]


async def test_cookie_mode_opens_one_session_for_a_burst_of_refusals(
    mock_api: respx.MockRouter,
) -> None:
    # Five calls in flight when the session lapses, each refused once, all
    # five queueing for the same refresh. Only the first has a token to
    # replace; the rest have to notice that the jar already holds one they
    # have not tried, or every one of them mints a session and leaves it open
    # on the device.
    tokens = itertools.chain([TOKEN], itertools.repeat(OTHER_TOKEN))
    login = mock_api.post("/api/auth/login").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=OK,
            headers={"Set-Cookie": f"auth_token={next(tokens)}; Path=/"},
        )
    )

    # Hold every refused call open until all five have arrived, so they really
    # are in flight together rather than one after another.
    in_flight = asyncio.Event()
    arrived = 0

    async def answer(request: httpx.Request) -> httpx.Response:
        nonlocal arrived
        if f"auth_token={TOKEN}" not in request.headers.get("Cookie", ""):
            return httpx.Response(200, json=OK)
        arrived += 1
        if arrived == 5:
            in_flight.set()
        await in_flight.wait()
        return httpx.Response(403, json=OK)

    atx = mock_api.get("/api/atx").mock(side_effect=answer)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await asyncio.gather(*(kvm.request("GET", "/api/atx") for _ in range(5)))
    assert login.call_count == 2
    assert atx.call_count == 10
    # Five refused, five retried — and the retries all carry the one token the
    # single refresh produced. Order is not asserted: the five wake together.
    carried = [call.request.headers["Cookie"] for call in atx.calls]
    assert sum(f"auth_token={TOKEN}" in cookie for cookie in carried) == 5
    assert sum(f"auth_token={OTHER_TOKEN}" in cookie for cookie in carried) == 5


async def test_cookie_mode_gives_up_after_one_retry(
    mock_api: respx.MockRouter,
) -> None:
    # A wrong password refuses every session it opens. One retry proves the
    # token was not the problem; a second would loop.
    login = _login_route(mock_api)
    atx = mock_api.get("/api/atx").mock(return_value=httpx.Response(403, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        with pytest.raises(AuthError):
            await kvm.request("GET", "/api/atx")
    assert login.call_count == 2
    assert atx.call_count == 2


async def test_cookie_mode_does_not_log_in_to_log_in(
    mock_api: respx.MockRouter,
) -> None:
    # /api/auth/login needs no credential, and routing it through the session
    # check would not terminate.
    login = _login_route(mock_api)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.auth.login("admin", "secret")
    assert login.call_count == 1


async def test_cookie_mode_does_not_log_in_to_log_out(
    mock_api: respx.MockRouter,
) -> None:
    # logout() aims at one particular token, which it has just put in the jar
    # itself. Refreshing the session on a refusal would drop the session this
    # client opened for the retry and report success for the one that was
    # asked about. No login route is registered on purpose: a login here
    # matches nothing and fails the test.
    logout = mock_api.post("/api/auth/logout").mock(
        return_value=httpx.Response(403, json=OK)
    )
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        with pytest.raises(AuthError):
            await kvm.auth.logout(OTHER_TOKEN)
        # The jar is left as it was found, which here is empty.
        assert kvm.cookies.get(_COOKIE) is None
    assert logout.call_count == 1
    assert f"auth_token={OTHER_TOKEN}" in logout.calls[0].request.headers["Cookie"]


async def test_cookie_mode_does_not_replay_a_streamed_body(
    mock_api: respx.MockRouter,
) -> None:
    # The retry would re-iterate an iterator the first attempt consumed, send
    # nothing, and fail with a ConfigurationError about the caller's `size` —
    # burying the refusal that actually happened.
    login = _login_route(mock_api)
    write = mock_api.post("/api/msd/write").mock(
        return_value=httpx.Response(403, json=OK)
    )

    async def image() -> AsyncIterator[bytes]:
        yield b"data"

    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        with pytest.raises(AuthError):
            await kvm.msd.upload("x.iso", image(), size=4)
    assert write.call_count == 1
    # The session was refreshed anyway, so the caller's own retry starts from
    # a token that has not been refused.
    assert login.call_count == 2


async def test_cookie_mode_reuses_a_token_put_there_by_hand(
    mock_api: respx.MockRouter,
) -> None:
    # A token saved from a previous run is a session that need not be opened
    # again. No login route is registered here on purpose: attempting one
    # would match nothing and fail the test on its own.
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        kvm.cookies.set("auth_token", OTHER_TOKEN, domain="pikvm.local", path="/")
        await kvm.request("GET", "/api/atx")
    assert [call.request.url.path for call in mock_api.calls] == ["/api/atx"]
    assert f"auth_token={OTHER_TOKEN}" in mock_api.calls[-1].request.headers["Cookie"]


async def test_websocket_carries_the_same_credential() -> None:
    async with PiKVM(URL, user="admin", passwd="secret") as kvm:
        assert kvm.ws()._credential_headers() == {
            "X-KVMD-User": "admin",
            "X-KVMD-Passwd": "secret",
        }
    async with PiKVM(URL, user="admin", passwd="secret", auth="basic") as kvm:
        expected = base64.b64encode(b"admin:secret").decode("ascii")
        assert kvm.ws()._credential_headers() == {"Authorization": f"Basic {expected}"}


async def test_websocket_without_a_session_says_what_to_do() -> None:
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        with pytest.raises(ConfigurationError, match="no session token"):
            kvm.ws()


async def test_websocket_uses_the_session_token(mock_api: respx.MockRouter) -> None:
    _login_route(mock_api)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.auth.login("admin", "secret")
        assert kvm.ws()._credential_headers() == {"Cookie": f"auth_token={TOKEN}"}
