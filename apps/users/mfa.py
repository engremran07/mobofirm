"""
apps.users.mfa

Time-based one-time passwords (RFC 6238, built on RFC 4226 HOTP) and the
``otpauth://`` provisioning URI that authenticator apps import from a QR code.

Policy ("is MFA required?") lives in ``apps.users.services.mfa``; this module only
generates secrets, verifies codes and builds the provisioning URI.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import time
from typing import Final
from urllib.parse import quote, urlencode

from django.db import DatabaseError

logger = logging.getLogger(__name__)

# Issuer values that mean "not configured": empty, or the historic model default.
UNSET_ISSUERS: Final[frozenset[str]] = frozenset({"", "Site"})
FALLBACK_ISSUER: Final[str] = "Mobofirm"


# =====================================================================
# BASE32 HELPERS
# =====================================================================
def _base32_pad(secret: str) -> str:
    """Normalise (spaces, case) and pad a Base32 secret to a multiple of 8."""
    normalised = secret.strip().replace(" ", "").upper()
    if not normalised:
        raise ValueError("Empty Base32 secret.")
    return normalised + "=" * ((-len(normalised)) % 8)


def _base32_decode(secret: str) -> bytes:
    """Decode a Base32 secret; raises ``ValueError`` without leaking the secret."""
    try:
        return base64.b32decode(_base32_pad(secret), casefold=True)
    except (binascii.Error, ValueError) as exc:
        logger.error("Invalid Base32 secret (decode failure).")
        raise ValueError("Invalid Base32 secret") from exc


# =====================================================================
# TOTP (RFC 6238)
# =====================================================================
class TOTPService:
    """RFC 6238 TOTP compatible with Google Authenticator, Authy and Microsoft
    Authenticator (SHA-1, 6 digits, 30 second steps)."""

    @staticmethod
    def generate_secret(num_bytes: int = 20) -> str:
        """A random Base32 secret without ``=`` padding."""
        raw = secrets.token_bytes(num_bytes)
        return base64.b32encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _hotp_from_bytes(key: bytes, counter: int, digits: int = 6) -> str:
        """RFC 4226 HOTP using HMAC-SHA1."""
        message = counter.to_bytes(8, "big")
        digest = hmac.new(key, message, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        part = digest[offset : offset + 4]
        binary = int.from_bytes(part, "big") & 0x7FFFFFFF
        return str(binary % (10**digits)).zfill(digits)

    @staticmethod
    def generate_current_code(
        secret: str,
        period: int = 30,
        digits: int = 6,
        at_time: int | None = None,
    ) -> str:
        """The code for now (or for ``at_time``)."""
        timestamp = int(at_time if at_time is not None else time.time())
        return TOTPService._hotp_from_bytes(
            _base32_decode(secret), timestamp // period, digits
        )

    @staticmethod
    def verify(
        secret: str,
        code: str,
        tolerance: int = 1,
        period: int = 30,
        digits: int = 6,
    ) -> bool:
        """Whether ``code`` is valid within +/- ``tolerance`` steps."""
        return (
            TOTPService.matching_counter(
                secret, code, tolerance=tolerance, period=period, digits=digits
            )
            is not None
        )

    @staticmethod
    def matching_counter(
        secret: str,
        code: str,
        tolerance: int = 1,
        period: int = 30,
        digits: int = 6,
        at_time: int | None = None,
    ) -> int | None:
        """The matching time-step counter (current step preferred), or None.

        An unusable stored secret is logged and treated as "no match" so a damaged
        factor never authenticates anyone.
        """
        code_str = str(code).strip()
        if (
            len(code_str) != digits
            or not code_str.isascii()
            or not code_str.isdigit()
            or tolerance < 0
            or period <= 0
        ):
            return None
        try:
            key = _base32_decode(secret)
        except ValueError:
            return None
        current_counter = int(at_time if at_time is not None else time.time()) // period
        offsets = sorted(
            range(-tolerance, tolerance + 1),
            key=lambda offset: (abs(offset), offset),
        )
        for offset in offsets:
            candidate = current_counter + offset
            if candidate < 0:
                continue
            expected = TOTPService._hotp_from_bytes(key, candidate, digits)
            if hmac.compare_digest(expected, code_str):
                return candidate
        return None


# =====================================================================
# Provisioning URI
# =====================================================================
def totp_issuer() -> str:
    """Issuer shown in the authenticator app.

    The configured ``UsersSettings.mfa_totp_issuer`` wins; when it is unset the
    runtime site name is used, so the app lists the account as "Mobofirm: you@…".
    """
    from apps.site_settings.services import get_site_settings
    from apps.users.models import UsersSettings

    try:
        configured = (UsersSettings.get_solo().mfa_totp_issuer or "").strip()
    except DatabaseError:
        logger.exception("MFA issuer setting could not be loaded")
        configured = ""
    if configured not in UNSET_ISSUERS:
        return configured
    return (get_site_settings().site_name or "").strip() or FALLBACK_ISSUER


def provisioning_uri(
    secret: str,
    account: str,
    *,
    issuer: str | None = None,
    digits: int = 6,
    period: int = 30,
) -> str:
    """``otpauth://totp/<issuer>:<account>?secret=…`` for authenticator apps.

    The label is percent-encoded (spaces as ``%20``) and keeps the ``:`` between
    issuer and account, as the Key URI Format requires.
    """
    chosen_issuer = (issuer or totp_issuer()).replace(":", "")
    label = quote(f"{chosen_issuer}:{account}", safe=":@")
    params = urlencode(
        {
            "secret": _base32_pad(secret).replace("=", ""),
            "issuer": chosen_issuer,
            "algorithm": "SHA1",
            "digits": digits,
            "period": period,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{params}"


__all__ = ["TOTPService", "provisioning_uri", "totp_issuer"]
