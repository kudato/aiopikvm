"""Auth API — session authentication.

kvmd accepts four credential sources and tries them in this order: the
``X-KVMD-User``/``X-KVMD-Passwd`` headers, the ``auth_token`` cookie, HTTP
Basic, and the unix socket peer. The first one that is *present* decides the
request — a non-empty ``X-KVMD-User`` with the wrong password is refused
rather than retried against the cookie.

:class:`aiopikvm.PiKVM` always sends those headers, so the sessions opened
here are for other consumers: a browser, another tool, or an
:class:`httpx.AsyncClient` passed in as *http_client* that sends no
credential headers of its own.
"""

import re

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

        The token is also stored in :attr:`PiKVM.cookies`, scoped to the host
        it came from, and sent with every later request — though it decides
        nothing while this client keeps sending its credential headers. See
        :attr:`PiKVM.cookies` for what a session token is actually good for.

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

        token = response.cookies.get(_COOKIE) or ""
        if token:
            # httpx has already filed kvmd's cookie under the response's
            # domain, and a token restored by hand sits under none. The jar
            # keys on the domain, so both would survive under one name and
            # httpx's own lookup raises on that. Collapse them to this one.
            self._store_token(token, response.request.url.host)
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

        The cookie is dropped from :attr:`PiKVM.cookies` once kvmd confirms.
        On failure the jar is left as it was found — including when the token
        came from the argument rather than the jar — because the session is
        then in an unknown state, and throwing a token away is the one thing
        that cannot be undone. Clear it by hand —
        ``kvm.cookies.delete("auth_token")`` — for a token known to be dead.

        Args:
            token: Session token to drop, surrounding whitespace ignored.
                Defaults to the one :meth:`login` left in
                :attr:`PiKVM.cookies`.
            timeout: Per-call timeout in seconds.

        Raises:
            ConfigurationError: If no token was given and none is stored, or
                the token is not the 64 hexadecimal characters kvmd accepts.
            AuthError: The credentials this client sends were refused
                (HTTP 403). Since the call always carries a cookie, kvmd
                answers from whichever source it checks first: for a client
                with a password that is the headers, not the token, and the
                session is left alone.
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
        host = self._client.base_url.host
        previous = self._stored_token()
        self._store_token(token, host)
        try:
            await self._post("/api/auth/logout", timeout=timeout)
        except Exception:
            # Put back whatever the client was carrying: dropping somebody
            # else's session must not cost this one its own credential.
            self._client.cookies.delete(_COOKIE)
            if previous:
                self._store_token(previous, host)
            raise
        self._client.cookies.delete(_COOKIE)

    def _store_token(self, token: str, host: str) -> None:
        """Make *token* the one session cookie the client carries.

        Args:
            token: Session token to store.
            host: Host to scope the cookie to. Without one httpx offers it to
                every server the client talks to, which for a shared client
                or a cross-host redirect means handing the session to
                somewhere it does not belong.
        """
        self._client.cookies.delete(_COOKIE)
        self._client.cookies.set(_COOKIE, token, domain=host, path="/")

    def _stored_token(self) -> str:
        """Return the session token held by the client, if any.

        Walks the jar rather than calling ``httpx.Cookies.get``, which raises
        ``CookieConflict`` — outside the :class:`PiKVMError` hierarchy — when
        two cookies share a name under different domains. Should the jar hold
        more than one anyway, the last in jar order wins.

        Returns:
            The stored token, or ``""`` when there is none.
        """
        token = ""
        for cookie in self._client.cookies.jar:
            if cookie.name == _COOKIE:
                token = cookie.value or ""
        return token
