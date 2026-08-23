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


async def test_basic_mode_hands_kvmd_a_session_token_it_will_prefer(
    mock_api: respx.MockRouter,
) -> None:
    """A token in the jar outranks the Basic credential beside it (#147).

    kvmd reads the cookie before Basic, and `auth="basic"` sends no
    `X-KVMD-User` to stop it getting that far — so a session opened by
    `login()` decides every later request, not the password. That the jar
    goes out alongside `Authorization` is this test's half of it; what makes
    it matter is the ordering the module docstring states.

    Nothing renews such a token either: a refusal is reported as it comes,
    with no session opened to retry under, which `auth="cookie"` does do.
    """
    _login_route(mock_api)
    atx = mock_api.get("/api/atx").mock(
        return_value=httpx.Response(403, json={"ok": False, "result": {}})
    )
    async with PiKVM(URL, user="admin", passwd="secret", auth="basic") as kvm:
        await kvm.auth.login("admin", "secret")
        with pytest.raises(AuthError):
            await kvm.request("GET", "/api/atx")
    request = mock_api.calls[-1].request
    assert f"{_COOKIE}={TOKEN}" in request.headers["Cookie"]
    assert "Authorization" in request.headers
    assert atx.call_count == 1


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


async def test_cookie_mode_logs_in_for_a_stream(mock_api: respx.MockRouter) -> None:
    # A stream is the first thing a client does often enough — tailing the
    # log, watching MJPEG — and in cookie mode the X-KVMD-* headers are
    # deliberately absent, so without a session of its own it carries no
    # credential at all.
    login = _login_route(mock_api)
    log = mock_api.get("/api/log").mock(return_value=httpx.Response(200, text="line"))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        assert [line async for line in kvm.system.stream_log()] == ["line"]
    assert login.call_count == 1
    assert f"auth_token={TOKEN}" in log.calls[0].request.headers["Cookie"]
    # kvmd checks the headers before the session, so a stream that still
    # carried them would never reach the token this preamble went and got.
    assert "X-KVMD-User" not in log.calls[0].request.headers
    assert "Authorization" not in log.calls[0].request.headers


async def test_cookie_mode_opens_a_new_session_when_a_stream_is_refused(
    mock_api: respx.MockRouter,
) -> None:
    # Nothing has been yielded when the refusal arrives, so the connection is
    # simply made again — the caller never sees the first attempt.
    tokens = iter([TOKEN, OTHER_TOKEN])
    login = mock_api.post("/api/auth/login").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=OK,
            headers={"Set-Cookie": f"auth_token={next(tokens)}; Path=/"},
        )
    )
    answers = iter([httpx.Response(403, json=OK), httpx.Response(200, text="line")])
    log = mock_api.get("/api/log").mock(side_effect=lambda request: next(answers))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        assert [line async for line in kvm.system.stream_log()] == ["line"]
    assert login.call_count == 2
    assert log.call_count == 2
    assert f"auth_token={OTHER_TOKEN}" in log.calls[-1].request.headers["Cookie"]
    for call in log.calls:
        assert "X-KVMD-User" not in call.request.headers
        assert "Authorization" not in call.request.headers


async def test_cookie_mode_gives_up_on_a_stream_after_one_retry(
    mock_api: respx.MockRouter,
) -> None:
    login = _login_route(mock_api)
    log = mock_api.get("/api/log").mock(return_value=httpx.Response(403, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        with pytest.raises(AuthError):
            [line async for line in kvm.system.stream_log()]
    assert login.call_count == 2
    assert log.call_count == 2
    for call in log.calls:
        assert "X-KVMD-User" not in call.request.headers
        assert "Authorization" not in call.request.headers


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


async def test_cookie_mode_keeps_its_session_past_a_valueless_cookie(
    mock_api: respx.MockRouter,
) -> None:
    """A later response filing an empty `auth_token` must not lose it (#169).

    The jar is any response's to write, not the login's alone, and it drops
    the cookies a server clears properly — with ``Max-Age=0`` or a past
    expiry — so an empty one that survives to be read carries no
    instruction. Reading it as no token opens a fresh session for every
    request and leaves a socket refusing to open, both advising a login that
    has already happened.
    """
    login = _login_route(mock_api)
    atx = mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            200, json=OK, headers={"Set-Cookie": "auth_token=; Path=/api"}
        )
    )
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        for _ in range(3):
            await kvm.request("GET", "/api/atx")
        assert kvm.ws()._credential_headers() == {"Cookie": f"auth_token={TOKEN}"}
    assert (login.call_count, atx.call_count) == (1, 3)


async def test_cookie_mode_reads_the_last_session_cookie_and_only_that_name(
    mock_api: respx.MockRouter,
) -> None:
    """Which entry of a jar holding several is carried (#169).

    A login collapses the jar to one, so this ordering arises only when a
    later response files an `auth_token` of its own under another path — and
    then the newer one, which the jar keeps last, is the one kvmd has just
    handed out. A cookie of some other name is not a session token however
    late it arrives.
    """
    _login_route(mock_api)
    mock_api.get("/api/atx").mock(
        return_value=httpx.Response(
            200,
            json=OK,
            headers=[
                ("set-cookie", f"auth_token={OTHER_TOKEN}; Path=/api"),
                ("set-cookie", "session=decoy; Path=/api"),
            ],
        )
    )
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.request("GET", "/api/atx")
        assert [(c.path, c.name) for c in kvm.cookies.jar] == [
            ("/", _COOKIE),
            ("/api", _COOKIE),
            ("/api", "session"),
        ]
        assert kvm.ws()._credential_headers() == {"Cookie": f"auth_token={OTHER_TOKEN}"}


# --- authentication switched off on the device ------------------------


def _auth_off_login_route(mock_api: respx.MockRouter) -> respx.Route:
    """Answer `/api/auth/login` the way a kvmd with authentication off does.

    Derived from the recorded success rather than captured, for the reason
    `tests/test_auth.py` gives at the same case: the device the fixtures come
    from runs with authentication on, and turning it off to record this would
    lock everyone out of it. kvmd's login handler returns a bare
    ``make_json_response()`` when ``is_auth_enabled()`` is false, which is
    this envelope with no ``Set-Cookie`` beside it.
    """
    return mock_api.post("/api/auth/login").mock(
        return_value=httpx.Response(200, json=OK)
    )


async def test_cookie_mode_logs_in_once_against_an_auth_disabled_kvmd(
    mock_api: respx.MockRouter,
) -> None:
    """A device that hands out no token is not a session waiting to open (#170).

    The jar stays empty however often the login succeeds, so a client that
    reads only the jar asks again before every request, at double the round
    trips for the rest of its life.
    """
    login = _auth_off_login_route(mock_api)
    atx = mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        for _ in range(5):
            await kvm.request("GET", "/api/atx")
    assert (login.call_count, atx.call_count) == (1, 5)
    for call in atx.calls:
        assert "Cookie" not in call.request.headers
        assert "X-KVMD-User" not in call.request.headers
        assert "Authorization" not in call.request.headers


@pytest.mark.parametrize("factory", ["ws", "media_ws", "webrtc"])
async def test_every_socket_opens_against_an_auth_disabled_kvmd(
    factory: str, mock_api: respx.MockRouter
) -> None:
    """Carrying no credential is what such a device accepts (#170).

    All three factories are asked, the way they are for the token, because
    they are three copies of one forwarding block and have drifted before.
    """
    _auth_off_login_route(mock_api)
    mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=OK))
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.request("GET", "/api/atx")
        assert getattr(kvm, factory)()._credential_headers() == {}


@pytest.mark.parametrize("factory", ["ws", "media_ws", "webrtc"])
async def test_an_explicit_login_opens_a_socket_with_authentication_off(
    factory: str, mock_api: respx.MockRouter
) -> None:
    """The advice the refusal gives has to work (#170).

    A socket with nothing to carry says to call `login()` and open it after
    that. Against a device with authentication off there is no token for
    `login()` to bring back, so what it leaves behind must be the knowledge
    that none is coming.
    """
    _auth_off_login_route(mock_api)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        socket = getattr(kvm, factory)()
        with pytest.raises(ConfigurationError, match="no session token"):
            socket._credential_headers()
        assert await kvm.auth.login("admin", "secret") == ""
        assert socket._credential_headers() == {}


async def test_cookie_mode_recovers_when_authentication_is_switched_back_on(
    mock_api: respx.MockRouter,
) -> None:
    """What a device answering 401 settles (#170).

    Remembering that a device hands out no token would strand a client on it
    for good if nothing took it back. A refusal is the device saying its
    authentication is on after all, so the login skipped until then is due.
    """
    auth_on = False
    logins = 0

    def login(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        logins += 1
        if not auth_on:
            return httpx.Response(200, json=OK)
        return httpx.Response(
            200, json=OK, headers={"Set-Cookie": f"auth_token={TOKEN}; Path=/"}
        )

    def atx(request: httpx.Request) -> httpx.Response:
        carried = f"auth_token={TOKEN}" in request.headers.get("Cookie", "")
        if auth_on and not carried:
            return httpx.Response(401, json={"ok": False, "result": None})
        return httpx.Response(200, json=OK)

    mock_api.post("/api/auth/login").mock(side_effect=login)
    mock_api.get("/api/atx").mock(side_effect=atx)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        for _ in range(3):
            await kvm.request("GET", "/api/atx")
        assert (logins, kvm.ws()._credential_headers()) == (1, {})

        auth_on = True
        await kvm.request("GET", "/api/atx")
        assert kvm.ws()._credential_headers() == {"Cookie": f"auth_token={TOKEN}"}

        for _ in range(3):
            await kvm.request("GET", "/api/atx")
    assert logins == 2


async def test_a_recovered_session_is_not_still_remembered_as_auth_off(
    mock_api: respx.MockRouter,
) -> None:
    """The remembering has to be undone, not merely overtaken (#170).

    Once a token is back in the jar it is read before the flag, so a client
    that never took the flag back looks recovered while it is not. What
    tells them apart is the session ending: with the jar empty again the
    flag decides, and a stale one skips the login and spends a refusal
    finding out.
    """
    logins = 0

    def login(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        logins += 1
        if logins == 1:  # the device still has authentication off
            return httpx.Response(200, json=OK)
        return httpx.Response(
            200, json=OK, headers={"Set-Cookie": f"auth_token={TOKEN}; Path=/"}
        )

    def atx(request: httpx.Request) -> httpx.Response:
        if f"auth_token={TOKEN}" in request.headers.get("Cookie", ""):
            return httpx.Response(200, json=OK)
        return httpx.Response(401, json={"ok": False, "result": None})

    mock_api.post("/api/auth/login").mock(side_effect=login)
    atx_route = mock_api.get("/api/atx").mock(side_effect=atx)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        # Authentication comes on with the very first request: one refusal,
        # one more login, and the retry carries the token it minted.
        await kvm.request("GET", "/api/atx")
        assert (logins, atx_route.call_count) == (2, 2)

        # The session ends — kvmd expired it, or something logged out.
        kvm.cookies.delete(_COOKIE)
        await kvm.request("GET", "/api/atx")

    # Three logins, and the last request cost one attempt, not a refusal and
    # then a retry: the client knew it had to log in rather than guessing.
    assert (logins, atx_route.call_count) == (3, 3)


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
    """Building the socket is fine; it is the handshake that needs a token."""
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        ws = kvm.ws()
        with pytest.raises(ConfigurationError, match="no session token"):
            ws._credential_headers()


async def test_websocket_on_a_client_that_was_never_entered() -> None:
    """There is no cookie jar to read at all, which the message says."""
    ws = PiKVM(URL, user="admin", passwd="secret", auth="cookie").ws()
    with pytest.raises(ConfigurationError, match="has not been entered"):
        ws._credential_headers()


@pytest.mark.parametrize("factory", ["ws", "media_ws", "webrtc"])
async def test_every_socket_reads_the_token_at_the_handshake(
    factory: str, mock_api: respx.MockRouter
) -> None:
    """Built before the login every guide does after it, and all three alike.

    The three factories are three copies of the same forwarding block, and
    they have drifted apart before, so each is asked the same question.
    """
    _login_route(mock_api)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        socket = getattr(kvm, factory)()
        with pytest.raises(ConfigurationError, match="no session token"):
            socket._credential_headers()
        await kvm.auth.login("admin", "secret")
        assert socket._credential_headers() == {"Cookie": f"auth_token={TOKEN}"}


async def test_websocket_uses_the_session_token(mock_api: respx.MockRouter) -> None:
    _login_route(mock_api)
    async with PiKVM(URL, user="admin", passwd="secret", auth="cookie") as kvm:
        await kvm.auth.login("admin", "secret")
        assert kvm.ws()._credential_headers() == {"Cookie": f"auth_token={TOKEN}"}
