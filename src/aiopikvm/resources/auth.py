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

from aiopikvm._base_resource import BaseResource
from aiopikvm._exceptions import APIError, AuthError, ConfigurationError

_COOKIE = "auth_token"
"""Name of the cookie kvmd stores its session token in."""


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

        The token is stored in :attr:`PiKVM.cookies` and sent with every
        later request, so nothing has to be done with the return value; it is
        returned for the case where a session should outlive this client
        object and be restored later.

        Args:
            user: kvmd user name. Must match ``^[a-z_]([a-z0-9@._-]*[a-z0-9_-])?$``
                — kvmd rejects anything else outright.
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
            ConfigurationError: If ``expire`` is negative.
            AuthError: The credentials were refused (HTTP 403), or kvmd's
                validators rejected the user name or password before checking
                them (HTTP 400).
        """
        if expire < 0:
            raise ConfigurationError(
                f"expire must be 0 (unlimited) or a positive number of seconds, "
                f"got {expire}"
            )
        password = passwd if totp is None else f"{passwd}{totp}"
        try:
            await self._post(
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
        return self._client.cookies.get(_COOKIE) or ""

    async def check(self) -> None:
        """Verify that the current credentials are accepted.

        Returns nothing: kvmd answers with an empty envelope, and the whole
        result is whether it answered at all.

        Raises:
            AuthError: No credentials were accepted (HTTP 401), or the ones
                sent were rejected (HTTP 403) — including a session token
                that has expired or been logged out.
        """
        await self._get("/api/auth/check")

    async def logout(
        self, token: str | None = None, *, timeout: float | None = None
    ) -> None:
        """Close a session.

        kvmd identifies the session to drop by the ``auth_token`` cookie
        alone; the ``X-KVMD-*`` headers get the request past the auth chain
        but say nothing about which session is meant, so a call with no
        cookie fails with HTTP 400.

        Args:
            token: Session token to drop. Defaults to the one
                :meth:`login` left in :attr:`PiKVM.cookies`.
            timeout: Per-call timeout in seconds.

        Raises:
            ConfigurationError: If no token was given and none is stored.
            AuthError: The token is unknown to kvmd, because it expired or
                was already logged out (HTTP 403).
        """
        if token is not None:
            self._client.cookies.set(_COOKIE, token)
        elif not self._client.cookies.get(_COOKIE):
            raise ConfigurationError(
                "logout() needs a session token: pass one, or call login() "
                "first so the client has one to drop."
            )
        try:
            await self._post("/api/auth/logout", timeout=timeout)
        except AuthError:
            # kvmd does not recognise the token, so the session is gone
            # either way and keeping the cookie would only fail every later
            # request made without a password. A transport failure is left
            # alone: the session may well still be alive.
            self._client.cookies.delete(_COOKIE)
            raise
        self._client.cookies.delete(_COOKIE)
