"""Time-based one-time passwords, RFC 6238.

kvmd verifies the code with ``pyotp.TOTP(secret).verify(code, valid_window=1)``
and takes the secret from ``/etc/kvmd/totp.secret``, which ``kvmd-totp init``
fills with ``pyotp.random_base32()``. That fixes every parameter: HMAC-SHA1,
six digits, a thirty-second step, and a base32 secret. Those are the defaults
here, and there is no reason to change them for a PiKVM.

This exists so that [`PiKVM`][aiopikvm.PiKVM] can be handed something that
still knows the code an hour from now. A code is good for one step — kvmd
allows the neighbouring two as well — so a client built with a literal one
stops authenticating about a minute later.
"""

import base64
import binascii
import hashlib
import hmac
import struct
import time

from aiopikvm._exceptions import ConfigurationError

DEFAULT_DIGITS = 6
"""How many digits kvmd reads: it takes ``passwd[-6:]`` as the code."""

DEFAULT_INTERVAL = 30
"""Seconds per step, which is ``pyotp``'s default and kvmd keeps it."""


class TOTP:
    """The current code for a shared secret, recomputed on every call.

    Pass one to [`PiKVM`][aiopikvm.PiKVM] as *totp* and the code is worked
    out per request rather than frozen at construction::

        from aiopikvm import PiKVM, TOTP

        async with PiKVM(url, passwd="secret", totp=TOTP(secret)) as kvm:
            ...

    Any zero-argument callable returning a string works there too; this one
    is for the ordinary case where the secret is what is on hand.

    Attributes:
        digits: Length of the code.
        interval: Seconds each code is valid for.
    """

    __slots__ = ("_key", "digits", "interval")

    def __init__(
        self,
        secret: str,
        *,
        digits: int = DEFAULT_DIGITS,
        interval: int = DEFAULT_INTERVAL,
    ) -> None:
        """Prepare a generator.

        Args:
            secret: The shared secret, base32 as ``kvmd-totp show`` prints
                it. Spaces and case are ignored, and the padding ``=`` that
                ``pyotp`` leaves off is added back.
            digits: Length of the code. kvmd reads six.
            interval: Seconds per step. kvmd uses thirty.

        Raises:
            ConfigurationError: If the secret is not base32, or *digits* or
                *interval* is not positive.
        """
        if digits < 1:
            raise ConfigurationError(f"digits must be positive, got {digits}")
        if interval < 1:
            raise ConfigurationError(f"interval must be positive, got {interval}")
        cleaned = secret.strip().replace(" ", "").upper()
        if not cleaned:
            raise ConfigurationError("The TOTP secret is empty")
        try:
            self._key = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
        except (binascii.Error, ValueError) as exc:
            raise ConfigurationError(
                f"The TOTP secret is not base32: {exc}. It is what "
                f"'kvmd-totp show' prints on the device, next to the QR code."
            ) from exc
        self.digits = digits
        self.interval = interval

    def __call__(self) -> str:
        """Return the code for right now.

        Returns:
            The code, zero-padded to *digits*.
        """
        return self.at(time.time())

    def at(self, timestamp: float) -> str:
        """Return the code for a point in time.

        Args:
            timestamp: Unix time the code should be valid at.

        Returns:
            The code, zero-padded to *digits*.
        """
        counter = int(timestamp // self.interval)
        mac = hmac.new(self._key, struct.pack(">Q", counter), hashlib.sha1).digest()
        # Dynamic truncation, RFC 4226 section 5.3: the low nibble of the
        # last byte picks where to read four bytes from, and the top bit is
        # masked off so the result does not depend on signed arithmetic.
        offset = mac[-1] & 0x0F
        code = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(code % 10**self.digits).zfill(self.digits)
