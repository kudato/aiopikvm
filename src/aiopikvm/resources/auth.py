"""Auth API — session authentication."""

from typing import Any

from aiopikvm._base_resource import BaseResource


class AuthResource(BaseResource):
    """Session-based authentication for PiKVM."""

    async def login(self, user: str, passwd: str, totp: str | None = None) -> Any:
        """Log in with credentials.

        Args:
            user: Username.
            passwd: Password.
            totp: Optional TOTP code (concatenated to password).

        Returns:
            The login result from the API.
        """
        password = passwd if totp is None else f"{passwd}{totp}"
        return await self._post(
            "/api/auth/login",
            json={"user": user, "passwd": password},
        )

    async def check(self) -> Any:
        """Check authentication status.

        Returns:
            The auth check result from the API.
        """
        return await self._get("/api/auth/check")

    async def logout(self) -> Any:
        """Log out of the current session.

        Returns:
            The logout result from the API.
        """
        return await self._post("/api/auth/logout")
