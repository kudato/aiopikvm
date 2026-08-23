"""AuthResource tests.

Every mocked response is a step of the ``auth_roundtrip`` scenario, recorded
against kvmd 4.206. The request side matters as much as the response here:
kvmd reads ``/auth/login`` with a form parser and identifies the session to
drop by cookie, and getting either wrong fails on a real device while a
mock happily accepts it.
"""

import http.cookiejar
import urllib.request
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from aiopikvm import (
    AuthError,
    ConfigurationError,
    ConnectError,
    PiKVM,
    ResponseError,
)
from aiopikvm.resources.auth import _COOKIE, _cookie_host
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
    """kvmd parses login with aiohttp's form parser, so JSON arrives empty.

    Both content types were sent to the device. The form one is the recorded
    success; the JSON one came back 400, the validator complaining about an
    empty user name — the fields never reached the handler at all. So the
    content type is read off the recording rather than spelled out here, and
    the one recorded as refused is named beside it (#177).
    """
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    await client.auth.login("admin", "secret")

    request = mock_api.calls[-1].request
    sent = request.headers["content-type"]
    assert sent == step("login_form_body")["request_content_type"]
    assert sent != step("login_json_body")["request_content_type"]
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
    """With authentication disabled kvmd answers 200 and sets no cookie.

    Derived from the recorded success step rather than captured: the device
    the fixtures come from runs with authentication on, and turning it off
    to record this would lock everyone out of it. kvmd's login handler
    returns a bare ``make_json_response()`` when ``is_auth_enabled()`` is
    false, which is exactly this response minus the cookie.
    """
    mock_api.post("/api/auth/login").mock(return_value=replay("login_form_body"))
    assert await client.auth.login("admin", "secret") == ""


async def test_login_over_a_restored_session_does_not_trip_httpx(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Restoring a token by hand stores it without a domain while kvmd's own
    Set-Cookie carries one. Two entries of the same name make
    ``httpx.Cookies.get`` raise ``CookieConflict``, which is outside the
    PiKVMError hierarchy."""
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    client.cookies.set("auth_token", "a" * 64)

    assert await client.auth.login("admin", "secret") == TOKEN
    assert sum(c.name == "auth_token" for c in client.cookies.jar) == 1


def _two_cookie_login(first: str, second: str) -> httpx.Response:
    """Build the recorded login success with two session cookies on it.

    Only the ``Set-Cookie`` pair is invented: the status and body are the
    recorded ones, and no device this project can record from puts two of
    them there — a proxy in front of kvmd setting its own under another path
    is what does.

    Args:
        first: Value of the cookie under ``Path=/``.
        second: Value of the cookie under ``Path=/api``, which the jar keeps
            last and httpx sends first.

    Returns:
        The response, carrying both under the one name.
    """
    entry = step("login_form_body")
    return httpx.Response(
        entry["status"],
        json=entry["body"],
        headers=[
            ("set-cookie", f"auth_token={first}; Path=/"),
            ("set-cookie", f"auth_token={second}; Path=/api"),
        ],
    )


async def test_login_survives_two_session_cookies_on_one_response(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Two `auth_token` cookies on one response must not escape as httpx's (#169).

    `httpx.Cookies.get` raises `CookieConflict` on that, which is outside the
    PiKVMError hierarchy — the rule CLAUDE.md states without qualification.
    The jar is walked instead, the last entry winning, which is how the
    client's own jar is already read.
    """
    mock_api.post("/api/auth/login").mock(
        return_value=_two_cookie_login("a" * 64, TOKEN)
    )

    assert await client.auth.login("admin", "secret") == TOKEN
    assert sum(c.name == "auth_token" for c in client.cookies.jar) == 1


async def test_a_valueless_cookie_does_not_mask_the_token_beside_it(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """An empty `auth_token` read last must not read as no token at all (#169).

    A cookie still in the jar was not cleared — the jar drops the ones a
    server clears, with ``Max-Age=0`` or an expiry in the past. Letting an
    empty one have the last word returns ``""``, which `login()` documents
    as kvmd running with authentication switched off, for a response that
    just opened a session.
    """
    mock_api.post("/api/auth/login").mock(return_value=_two_cookie_login(TOKEN, ""))

    assert await client.auth.login("admin", "secret") == TOKEN
    assert sum(c.name == "auth_token" for c in client.cookies.jar) == 1


async def test_login_reads_the_session_cookie_and_not_a_neighbour(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A cookie of another name is not the token, however late it lands (#169).

    The walk takes the last match, and the jar groups cookies by path in the
    order the paths first appear — length is not what orders them — so the
    decoy is last here by having the second path named.
    """
    entry = step("login_form_body")
    mock_api.post("/api/auth/login").mock(
        return_value=httpx.Response(
            entry["status"],
            json=entry["body"],
            headers=[
                ("set-cookie", f"auth_token={TOKEN}; Path=/"),
                ("set-cookie", "session=decoy; Path=/api"),
            ],
        )
    )

    assert await client.auth.login("admin", "secret") == TOKEN


async def test_cookie_mode_survives_two_session_cookies_it_never_asked_for(
    mock_api: respx.MockRouter,
) -> None:
    """The same conflict, reached without the caller calling `login()` (#169).

    Under `auth="cookie"` the first request opens the session itself, so
    `request()` walks into `_ensure_session()` and into `login()`. What
    escaped was httpx's exception, out of a call the caller never made —
    which is why this path is worth its own test rather than being covered
    by the one above.
    """
    login = mock_api.post("/api/auth/login").mock(
        return_value=_two_cookie_login("a" * 64, TOKEN)
    )
    atx = mock_api.get("/api/atx").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )

    async with PiKVM(
        "https://pikvm.local", user="admin", passwd="secret", auth="cookie"
    ) as kvm:
        await kvm.request("GET", "/api/atx")

    # Compared whole, not searched: both cookies reaching the wire would
    # contain this one and say nothing about which was chosen.
    assert (login.call_count, atx.call_count) == (1, 1)
    assert atx.calls[-1].request.headers["Cookie"] == f"auth_token={TOKEN}"


async def test_login_returns_this_response_token_not_a_stale_one(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A leftover cookie must not be mistaken for a session kvmd just
    declined to open."""
    mock_api.post("/api/auth/login").mock(return_value=replay("login_form_body"))
    client.cookies.set("auth_token", "a" * 64)

    assert await client.auth.login("admin", "secret") == ""


async def test_login_scopes_the_token_to_the_device(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A cookie stored without a domain is offered to every host the client
    talks to — a shared http_client or a cross-host redirect would hand the
    session somewhere it does not belong."""
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    await client.auth.login("admin", "secret")

    assert [c.domain for c in client.cookies.jar] == ["pikvm.local"]
    elsewhere = httpx.Request("GET", "https://example.com/")
    client.cookies.set_cookie_header(elsewhere)
    assert "cookie" not in elsewhere.headers


@pytest.mark.parametrize(
    ("host", "domain"),
    [
        pytest.param("pikvm.local", "pikvm.local", id="dotted"),
        pytest.param("pikvm", "pikvm.local", id="dotless"),
        pytest.param("pikvm:8443", "pikvm.local", id="dotless_with_port"),
        pytest.param("127.0.0.1", "127.0.0.1", id="ipv4"),
        pytest.param("[::1]", "[::1].local", id="ipv6"),
        pytest.param("пиквм.рф", "xn--b1algjl.xn--p1ai", id="idn"),
    ],
)
async def test_login_scopes_the_token_where_the_jar_will_match_it(
    host: str, domain: str
) -> None:
    """A cookie the jar withholds is a session the device never sees (#178).

    ``http.cookiejar`` matches a cookie's domain against the *effective*
    request host, not the one as written: a name with no dot in it has
    ``.local`` appended before the comparison, an IPv6 literal keeps the
    brackets the URL strips, and an internationalised name is punycode on the
    wire. Filing the token under the host as written therefore offered it to
    that host's subdomains and never to the device itself, so
    ``auth="cookie"`` could not authenticate at all.

    The domain is asserted as well as the outcome because the two fail apart:
    the jar hands a cookie with **no** domain to everybody, which is the
    other half of what this scoping is for and would satisfy the round trip
    below on its own.

    Args:
        host: Host of the base URL the client is built with.
        domain: The name the jar has to have filed the cookie under.
    """
    base = f"https://{host}"
    with respx.mock(base_url=base) as router:
        router.post("/api/auth/login").mock(
            return_value=replay("login_form_body", cookie=TOKEN)
        )
        async with PiKVM(base, user="admin", passwd="admin") as kvm:
            await kvm.auth.login("admin", "secret")

            assert [c.domain for c in kvm.cookies.jar] == [domain]
            back = httpx.Request("GET", f"{base}/api/atx")
            kvm.cookies.set_cookie_header(back)
            assert back.headers["cookie"] == f"auth_token={TOKEN}"


@pytest.mark.parametrize(
    "host",
    [
        "pikvm.local",
        "pikvm",
        "pikvm:8443",
        "PiKVM.LOCAL",
        "127.0.0.1",
        "127.0.0.1:8443",
        "[::1]",
        "[::1]:8443",
        "[::ffff:127.0.0.1]",
        "kvm.example.com",
        "пиквм.рф",
        "xn--b1algjl.xn--p1ai",
    ],
)
def test_the_cookie_scope_is_the_one_the_standard_library_computes(host: str) -> None:
    """`_cookie_host` spells out a rule it does not own, so pin it (#178).

    ``http.cookiejar.eff_request_host()`` is the rule itself, but it is not
    part of what the module declares — calling it from typed code takes an
    ignore — and it reads the whole netloc, userinfo and all. So the rule is
    restated in `_cookie_host`, and this is what keeps the restatement
    honest: it is compared against the standard library over every shape of
    host a device is reached by, the ones the URL and the jar disagree about
    included.

    The comparison is against ``str(httpx.URL(...))``, not the string that
    was typed, because that is what the jar sees — httpx normalises an
    internationalised name to punycode before it goes on the wire, and asking
    the standard library about the typed form would pin the rule to a URL
    nothing ever sends. The last two hosts are the same device written both
    ways, and they are what says so.

    Args:
        host: Host of the base URL, port and brackets as written.
    """
    url = httpx.URL(f"https://{host}")
    _, effective = http.cookiejar.eff_request_host(urllib.request.Request(str(url)))
    assert _cookie_host(url) == effective


async def test_login_leaves_no_password_in_the_jar_from_a_userinfo_url() -> None:
    """A credential in the base URL must not become a cookie's domain.

    httpx sends the userinfo, so the jar asks about
    ``admin:s3cret@pikvm.local`` and files kvmd's ``Set-Cookie`` under that
    by itself, before this resource sees the response. Scoping the token
    afresh is what takes it back out — `PiKVM.cookies` is public, and
    anything that prints the jar prints a domain in full, a ``repr()`` or
    `http.cookiejar.MozillaCookieJar.save()` included.

    A session for a device addressed this way then does not work at all: the
    jar will not answer ``admin:s3cret@pikvm.local`` with a cookie scoped to
    ``pikvm.local``. That is how it was before #178 and how it still is —
    there is no domain here that is both safe and matched — and it is not
    asserted below, so that fixing it does not have to fight a test.
    """
    base = "https://admin:s3cret@pikvm.local"
    with respx.mock(base_url="https://pikvm.local") as router:
        router.post("/api/auth/login").mock(
            return_value=replay("login_form_body", cookie=TOKEN)
        )
        async with PiKVM(base, user="admin", passwd="admin") as kvm:
            await kvm.auth.login("admin", "secret")
            assert [c.domain for c in kvm.cookies.jar] == ["pikvm.local"]


async def test_login_stores_a_token_no_kvmd_would_send() -> None:
    """Storing a token is storing a value, not writing a header.

    A token is read out of a header, and headers arrive as bytes: kvmd sends
    64 hex characters, but the client treats the response as untrusted
    everywhere else — the ``ResponseError`` branch beside this one exists for
    a 200 that did not come from kvmd at all. Building the cookie out of a
    header string instead of storing the value would make a non-ASCII byte
    here a bare ``UnicodeEncodeError`` from httpx's header encoder, raised
    out of `login()` itself and out of the implicit login inside any request
    under ``auth="cookie"``. That is what this holds shut.

    It does not make such a token *usable*: the request after this one still
    dies encoding the cookie header httpx builds from the jar, with the same
    exception from the same encoder. That escape is httpx's own and predates
    this client's handling of the token — what changed here is that storing
    the token is no longer itself the thing that raises.
    """
    odd = "caf\xe9" + "b" * 60
    entry = step("login_form_body")
    with respx.mock(base_url="https://pikvm.local") as router:
        router.post("/api/auth/login").mock(
            return_value=httpx.Response(
                entry["status"],
                json=entry["body"],
                headers=[(b"set-cookie", f"auth_token={odd}; Path=/".encode())],
            )
        )
        async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
            assert await kvm.auth.login("admin", "secret") == odd
            assert [c.value for c in kvm.cookies.jar] == [odd]


async def test_logout_keeps_the_jar_when_there_is_no_host_to_scope_to() -> None:
    """The scope is worked out before the jar is touched, and has to be.

    `logout()` files the token it is dropping, so a client whose base URL
    names no host — one built without a scheme, or an external
    ``http_client`` built without a ``base_url`` — reaches the one place in
    this resource that can refuse. Refusing after the delete would empty the
    jar of the credential the client came in with, which is what its own
    docstring promises never to do on a failed logout.
    """
    async with PiKVM("pikvm.local", user="admin", passwd="admin") as kvm:
        kvm.cookies.set(_COOKIE, TOKEN, domain="pikvm.local", path="/")
        with pytest.raises(ConfigurationError, match="names no host"):
            await kvm.auth.logout("b" * 64)
        assert [c.value for c in kvm.cookies.jar] == [TOKEN]


async def test_logout_restores_the_jar_when_it_fails(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Dropping somebody else's session must not cost this client its own
    credential, which for a header-less client is the cookie itself."""
    mock_api.post("/api/auth/logout").mock(return_value=replay("logout_refused"))
    client.cookies.set("auth_token", TOKEN)

    with pytest.raises(AuthError):
        await client.auth.logout("b" * 64)
    assert client.cookies.get("auth_token") == TOKEN


async def test_login_keeps_a_restored_token_when_it_fails(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Refreshing a restored session must not destroy it on the way. The
    token is the only handle on a session that is still alive server-side."""
    mock_api.post("/api/auth/login").mock(return_value=replay("login_wrong_passwd"))
    client.cookies.set("auth_token", TOKEN)

    with pytest.raises(AuthError):
        await client.auth.login("admin", "mistyped")
    assert client.cookies.get("auth_token") == TOKEN


async def test_login_keeps_a_restored_token_when_the_connection_fails(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/auth/login").mock(side_effect=httpx.ConnectError("boom"))
    client.cookies.set("auth_token", TOKEN)

    with pytest.raises(ConnectError):
        await client.auth.login("admin", "secret")
    assert client.cookies.get("auth_token") == TOKEN


async def test_login_rejects_a_200_that_is_not_kvmd(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """No cookie and no envelope means something else answered — a captive
    portal, a misrouted proxy. Reporting that as "authentication is off"
    would be worse than useless."""
    mock_api.post("/api/auth/login").mock(
        return_value=httpx.Response(200, html="<html>Sign in</html>")
    )
    with pytest.raises(ResponseError):
        await client.auth.login("admin", "secret")


async def test_login_rejects_an_expire_kvmd_cannot_read(client: PiKVM) -> None:
    """kvmd's validator caps the raw value at 16 characters and answers 400
    'RAW limit exceed' — a rejection that is not about credentials, so it
    must not reach the 400-to-AuthError translation."""
    with pytest.raises(ConfigurationError):
        await client.auth.login("admin", "secret", expire=10**17)


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


async def test_logout_reaches_a_dotless_host_with_the_cookie() -> None:
    """logout() files the token itself, so it has the same scope to get right.

    kvmd identifies the session to drop by cookie and nothing else, so one
    the jar withholds is a call that cannot succeed — the recorded
    ``logout_without_cookie`` step is the 400 it answers with. This is the
    other side of `_store_token` from login: the token comes from the caller
    and the URL from the client, and both paths file it the same way (#178).
    """
    with respx.mock(base_url="https://pikvm") as router:
        route = router.post("/api/auth/logout").mock(return_value=replay("logout"))
        async with PiKVM("https://pikvm", user="admin", passwd="admin") as kvm:
            await kvm.auth.logout(TOKEN)

    assert route.calls[-1].request.headers["cookie"] == f"auth_token={TOKEN}"


async def test_logout_rejects_a_malformed_token(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """kvmd wants 64 hex characters; anything else is a 400 worth avoiding,
    and it would leave junk in the jar for every later request to send."""
    with pytest.raises(ConfigurationError):
        await client.auth.logout("not-a-token")
    assert not mock_api.calls
    assert client.cookies.get("auth_token") is None


async def test_logout_sends_one_cookie_after_a_login(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """Two auth_token cookies on the wire would leave kvmd's choice of
    session to jar ordering."""
    mock_api.post("/api/auth/login").mock(
        return_value=replay("login_form_body", cookie=TOKEN)
    )
    mock_api.post("/api/auth/logout").mock(return_value=replay("logout"))

    await client.auth.login("admin", "secret")
    await client.auth.logout("b" * 64)

    assert mock_api.calls[-1].request.headers["cookie"].count("auth_token=") == 1


async def test_logout_keeps_the_cookie_when_kvmd_refuses_the_call(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A 403 here is about the X-KVMD headers, not the token: kvmd stops at
    the first credential source present and never reaches the cookie, so the
    session is still alive and the token still the only copy of it."""
    mock_api.post("/api/auth/logout").mock(return_value=replay("logout_refused"))
    client.cookies.set("auth_token", TOKEN)

    with pytest.raises(AuthError):
        await client.auth.logout()
    assert client.cookies.get("auth_token") == TOKEN


async def test_logout_keeps_the_cookie_when_the_connection_fails(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The session may well still be alive, so the token stays usable."""
    mock_api.post("/api/auth/logout").mock(side_effect=httpx.ConnectError("boom"))
    client.cookies.set("auth_token", TOKEN)

    with pytest.raises(ConnectError):
        await client.auth.logout()
    assert client.cookies.get("auth_token") == TOKEN


async def test_logout_leaves_no_cookie_behind_when_it_was_given_one(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """A token passed in belongs to the caller, who still has it; the jar is
    put back the way it was rather than keeping somebody else's session."""
    mock_api.post("/api/auth/logout").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(ConnectError):
        await client.auth.logout(TOKEN)
    assert client.cookies.get("auth_token") is None
