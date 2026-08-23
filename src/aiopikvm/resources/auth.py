"""Auth API — session authentication.

kvmd accepts four credential sources and tries them in this order: the
``X-KVMD-User``/``X-KVMD-Passwd`` headers, the ``auth_token`` cookie, HTTP
Basic, and the unix socket peer. The first one that is *present* decides the
request — a non-empty ``X-KVMD-User`` with the wrong password is refused
rather than retried against the cookie.

Which source [`aiopikvm.PiKVM`][aiopikvm.PiKVM] leaves for a session token
depends on its *auth* mode. ``"headers"`` sends that pair with every request,
so a session opened here never authenticates it. ``"cookie"`` sends no
credential of its own and is authenticated by one throughout, sockets
included. ``"basic"`` sends no ``X-KVMD-User`` either, so kvmd reaches the
cookie before the ``Authorization`` header and a session opened here takes its
requests over — though not its sockets, which carry the password regardless.

A session is also for consumers other than this client — a browser, another
tool, or an `httpx.AsyncClient` passed in as *http_client* that sends no
credential of its own.
"""

import re

import httpx

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import (
    APIError,
    AuthError,
    ConfigurationError,
    ResponseError,
)

_COOKIE = "auth_token"
"""Name of the cookie kvmd stores its session token in."""

_TOKEN = re.compile(r"[0-9a-f]{64}")
"""The token body kvmd's ``valid_auth_token`` accepts.

kvmd strips its input before matching, so a token is compared here with the
surrounding whitespace removed too.
"""

_EXPIRE_DIGITS = 16
"""kvmd reads ``expire`` through a validator capped at 16 raw characters."""


def _token_in(cookies: httpx.Cookies) -> str:
    """Return the session token held in *cookies*, if any.

    Walks the jar rather than calling `httpx.Cookies.get`, which raises
    ``CookieConflict`` — outside the [`PiKVMError`][aiopikvm.PiKVMError]
    hierarchy — when two cookies share a name under different domains or
    paths. Should there be more than one anyway, the last in jar order wins,
    which is the rule the client's own jar is read by.

    A valueless entry is passed over rather than counted as the last word.
    The jar drops a cookie cleared the way a server clears one — with
    ``Max-Age=0`` or an expiry in the past — so an empty ``auth_token`` that
    survives to be read here carries no instruction, and letting it win
    would hide a real token behind it.

    Args:
        cookies: Jar to read: a response's, or the client's.

    Returns:
        The token, or ``""`` when there is none.
    """
    token = ""
    for cookie in cookies.jar:
        if cookie.name == _COOKIE and cookie.value:
            token = cookie.value
    return token


class AuthResource(BaseResource):
    """Session-based authentication for PiKVM."""

    async def login(
        self,
        user: str,
        passwd: str,
        totp: str | None = None,
        *,
        expire: int = 0,
        timeout: float | None = None,
    ) -> str:
        """Open a session and return its token.

        kvmd reads this endpoint with aiohttp's form parser, so the
        credentials travel as ``application/x-www-form-urlencoded``. It
        answers with an empty envelope and puts the token in a ``Set-Cookie:
        auth_token`` header — that header is the only place it ever appears.

        The token is also stored in [`PiKVM.cookies`][aiopikvm.PiKVM.cookies],
        scoped to the host it came from, and sent with every later request.
        Under ``auth="cookie"`` it is what those requests are authenticated
        by, and what a socket handshake carries. Under ``auth="basic"`` it
        takes over as well — kvmd reads the cookie before the Basic
        credential — and nothing renews it, so a client that outlives the
        session starts failing with a password that is still good. Only
        ``auth="headers"`` leaves it deciding nothing, that pair being read
        first; see [`PiKVM.cookies`][aiopikvm.PiKVM.cookies] for what a
        session token is good for in that case.

        A token already in the jar is replaced. The session it belonged to
        stays open on the device until it expires or any session of that user
        is logged out, so keep it if it is still wanted.

        Args:
            user: kvmd user name. Must match
                ``^[a-z_]([a-z0-9@._-]*[a-z0-9_-])?$`` — kvmd rejects
                anything else outright.
            passwd: Password, printable ASCII only.
            totp: Current TOTP code, appended to the password. kvmd validates
                the concatenation, so a code that has rotated since it was
                read fails like a wrong password.
            expire: Session lifetime in seconds. ``0`` asks for an unlimited
                session; kvmd caps both cases at the device-wide limit from
                its own config.
            timeout: Per-call timeout in seconds.

        Returns:
            The session token, 64 hexadecimal characters. Empty when kvmd
            runs with authentication switched off — there is no session to
            hand out, and every request would be served anyway.

        Raises:
            ConfigurationError: If ``expire`` is negative or longer than
                kvmd's validator accepts.
            AuthError: The credentials were refused (HTTP 403), or kvmd's
                validators rejected the user name or password before checking
                them (HTTP 400).
        """
        if expire < 0:
            raise ConfigurationError(
                f"expire must be 0 (unlimited) or a positive number of "
                f"seconds, got {expire}"
            )
        if len(str(expire)) > _EXPIRE_DIGITS:
            raise ConfigurationError(
                f"expire must be at most {_EXPIRE_DIGITS} digits, which is "
                f"what kvmd's validator reads, got {expire}"
            )
        password = passwd if totp is None else f"{passwd}{totp}"
        try:
            response = await self._post_raw(
                "/api/auth/login",
                data={"user": user, "passwd": password, "expire": str(expire)},
                timeout=timeout,
            )
        except AuthError:
            raise
        except APIError as exc:
            if exc.status_code == 400:
                # kvmd validates the user name against a regex and the
                # password against a printable-ASCII rule before it looks
                # either up, and reports both as a plain ValidatorError. The
                # only other field this call sends is expire, checked above.
                raise AuthError(
                    str(exc),
                    exc.status_code,
                    error=exc.error,
                    error_msg=exc.error_msg,
                ) from exc
            raise

        token = _token_in(response.cookies)
        if token:
            # httpx has already filed kvmd's cookie under the response's
            # domain, and a token restored by hand sits under none. The jar
            # keys on the domain, so both would survive under one name and
            # httpx's own lookup raises on that. Collapse them to this one.
            self._store_token(token, response.request.url)
            self._client._record_login(token)
            return token

        # No cookie means kvmd is running without authentication — or that
        # something which is not kvmd answered 200, and reporting that as
        # "authentication is switched off" would be worse than useless.
        try:
            body = response.json()
        except ValueError:
            body = None
        if not (isinstance(body, dict) and body.get("ok") is True):
            raise ResponseError(
                "/api/auth/login answered 200 with neither a session cookie "
                f"nor a kvmd envelope: {response.text[:200]}",
                response.status_code,
            )
        self._client._record_login("")
        return ""

    async def check(self, *, timeout: float | None = None) -> None:
        """Verify that the current credentials are accepted.

        Returns nothing: kvmd answers with an empty envelope, and the whole
        result is whether it answered at all.

        Args:
            timeout: Per-call timeout in seconds.

        Raises:
            AuthError: No credentials were accepted (HTTP 401), or the ones
                sent were rejected (HTTP 403) — including a session token
                that has expired or been logged out.
        """
        await self._get("/api/auth/check", timeout=timeout)

    async def logout(
        self, token: str | None = None, *, timeout: float | None = None
    ) -> None:
        """Close the sessions of the user a token belongs to.

        kvmd takes the token's owner and drops **every** session that user
        has, not just this one. A script tidying up after itself therefore
        signs the same account out of the web UI and invalidates any other
        token it holds.

        The session is identified by the ``auth_token`` cookie alone; the
        ``X-KVMD-*`` headers get the request past the auth chain but say
        nothing about which session is meant, so a call with no cookie fails
        with HTTP 400.

        The cookie is dropped from [`PiKVM.cookies`][aiopikvm.PiKVM.cookies]
        once kvmd confirms. On failure the jar is left as it was found —
        including when the token came from the argument rather than the jar —
        because the session is then in an unknown state, and throwing a token
        away is the one thing that cannot be undone. Clear it by hand —
        ``kvm.cookies.delete("auth_token")`` — for a token known to be dead.

        Args:
            token: Session token to drop, surrounding whitespace ignored.
                Defaults to the one
                [`login()`][aiopikvm.resources.auth.AuthResource.login] left
                in [`PiKVM.cookies`][aiopikvm.PiKVM.cookies].
            timeout: Per-call timeout in seconds.

        Raises:
            ConfigurationError: If no token was given and none is stored, or
                the token is not the 64 hexadecimal characters kvmd accepts.
            AuthError: The credentials this client sends were refused
                (HTTP 403). Since the call always carries a cookie, which
                source kvmd answers from follows the *auth* mode: under
                ``"headers"`` that pair is read first, so a token it does not
                know is not what refused the call and the session is left
                alone; under the other two it is the cookie, so the token
                being dropped is also what the call is authenticated by.
        """
        given = token is not None
        token = token.strip() if token is not None else self._stored_token()
        if not token:
            raise ConfigurationError(
                "logout() was given a blank session token"
                if given
                else "logout() needs a session token: pass one, or call "
                "login() first so the client has one to drop."
            )
        if not _TOKEN.fullmatch(token):
            detail = (
                "is not hexadecimal"
                if len(token) == 64
                else f"is {len(token)} characters long"
            )
            raise ConfigurationError(
                f"A kvmd session token is 64 hexadecimal characters; this one {detail}"
            )
        url = self._client.base_url
        previous = self._stored_token()
        self._store_token(token, url)
        try:
            await self._post("/api/auth/logout", timeout=timeout)
        except Exception:
            # Put back whatever the client was carrying: dropping somebody
            # else's session must not cost this one its own credential.
            self._client.cookies.delete(_COOKIE)
            if previous:
                self._store_token(previous, url)
            raise
        self._client.cookies.delete(_COOKIE)

    def _store_token(self, token: str, url: httpx.URL) -> None:
        """Make *token* the one session cookie the client carries.

        The cookie is filed by handing the jar a ``Set-Cookie`` from *url* —
        the path a token kvmd sends takes anyway — rather than by naming a
        domain. ``http.cookiejar`` matches a cookie against the *effective*
        request host, which is not always the host in the URL: a name with no
        dot in it has ``.local`` appended, and an IPv6 literal keeps the
        brackets `httpx.URL` strips. Naming the raw host is how a cookie
        comes to be withheld from the very device it was minted by — the jar
        asks about ``pikvm.local`` while the cookie says ``pikvm``, so it goes
        to that name's subdomains and never to the device (#178). Extracting
        it leaves that rule where it is defined instead of restating it here.

        Args:
            token: Session token to store. Must be non-empty and free of
                cookie punctuation, which is what both callers hold: one
                takes it from a cookie kvmd sent, the other has matched it
                against `_TOKEN` first.
            url: URL of the device to scope the cookie to. Unscoped, httpx
                offers it to every server the client talks to, which for a
                shared client or a cross-host redirect means handing the
                session to somewhere it does not belong.
        """
        self._client.cookies.delete(_COOKIE)
        self._client.cookies.extract_cookies(
            httpx.Response(
                200,
                headers={"set-cookie": f"{_COOKIE}={token}; Path=/"},
                request=httpx.Request("GET", url),
            )
        )

    def _stored_token(self) -> str:
        """Return the session token held by the client, if any.

        Read through `_token_in`, which says why the jar is walked rather
        than asked.

        Returns:
            The stored token, or ``""`` when there is none.
        """
        return _token_in(self._client.cookies)
