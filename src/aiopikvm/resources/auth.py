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
from aiopikvm._exceptions import APIError, AuthError, ConfigurationError

_COOKIE = "auth_token"
"""Name of the cookie kvmd stores its session token in."""

_TOKEN = re.compile(r"[0-9a-f]{64}")
"""What kvmd's ``valid_auth_token`` accepts; anything else is a 400."""

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

        The token is also stored in :attr:`PiKVM.cookies` and sent with every
        later request, though it decides nothing while this client keeps
        sending its credential headers. See :attr:`PiKVM.cookies` for what a
        session token is actually good for.

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
        # A token put here by hand is stored without a domain while kvmd's
        # own Set-Cookie carries one, and the jar keys on the domain. Clearing
        # first keeps the two from piling up under the same name.
        self._client.cookies.delete(_COOKIE)
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
        return response.cookies.get(_COOKIE) or ""

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

        The cookie is dropped from :attr:`PiKVM.cookies` once kvmd confirms,
        and kept otherwise: a failure here leaves the session in an unknown
        state, and throwing the token away would be the one thing that
        cannot be undone.

        Args:
            token: Session token to drop. Defaults to the one
                :meth:`login` left in :attr:`PiKVM.cookies`.
            timeout: Per-call timeout in seconds.

        Raises:
            ConfigurationError: If no token was given and none is stored, or
                the token is not the 64 hexadecimal characters kvmd accepts.
            AuthError: The credentials this client sends were refused
                (HTTP 401/403). Note that for a client with a password this
                is about the headers, not the token — kvmd never gets as far
                as the cookie, and the session is left alone.
        """
        token = token if token is not None else self._stored_token()
        if not token:
            raise ConfigurationError(
                "logout() needs a session token: pass one, or call login() "
                "first so the client has one to drop."
            )
        if not _TOKEN.fullmatch(token):
            raise ConfigurationError(
                "A kvmd session token is 64 hexadecimal characters; "
                f"this one is {len(token)} characters long"
            )
        self._client.cookies.delete(_COOKIE)
        self._client.cookies.set(_COOKIE, token)
        await self._post("/api/auth/logout", timeout=timeout)
        self._client.cookies.delete(_COOKIE)

    def _stored_token(self) -> str:
        """Return the session token held by the client, if any.

        Walks the jar rather than calling ``httpx.Cookies.get``, which raises
        ``CookieConflict`` — outside the :class:`PiKVMError` hierarchy — when
        two cookies share a name under different domains.

        Returns:
            The stored token, or ``""`` when there is none.
        """
        for cookie in self._client.cookies.jar:
            if cookie.name == _COOKIE:
                return cookie.value or ""
        return ""
