"""
apps.users.mfa

Enterprise-grade Multi-Factor Authentication (MFA) utilities.

✔ Django 5.2 / Python 3.12 compliant
✔ RFC 6238 (TOTP) + RFC 4226 (HOTP) compliant
✔ Timing-attack resistant comparisons
✔ Stable issuer rules with no branding
✔ Hardened Base32 handling (no secret leakage)
✔ Drift-tolerant window verification
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import quote_plus

from apps.site_settings.models import SiteSettings

logger = logging.getLogger(__name__)

DEFAULT_ISSUER = "Account Portal"


# =====================================================================
# BASE32 HELPERS
# =====================================================================
def _base32_pad(secret: str) -> str:
    """
    Normalize & pad Base32 secret for decoding.
    - Removes spaces
    - Uppercases
    - Pads to a multiple of 8 chars
    """
    s = secret.strip().replace(" ", "").upper()
    if not s:
        raise ValueError("Empty Base32 secret.")
    pad = (-len(s)) % 8
    return s + ("=" * pad)


def _base32_decode(secret: str) -> bytes:
    """
    Decode Base32 secret with strict safety.
    Logs errors without leaking the secret.
    Raises ValueError on failure.
    """
    try:
        padded = _base32_pad(secret)
        return base64.b32decode(padded, casefold=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        logger.exception("Invalid Base32 secret (decode failure).")
        raise ValueError("Invalid Base32 secret") from exc


# =====================================================================
# TOTP (RFC-6238)
# =====================================================================
class TOTPService:
    """
    RFC 6238 TOTP implementation.
    Generates TOTP codes compatible with:
        - Google Authenticator
        - Authy
        - Microsoft Authenticator

    Public methods:
        generate_secret()
        generate_current_code()
        verify()
    """

    @staticmethod
    def generate_secret(num_bytes: int = 20) -> str:
        """
        Generate a secure Base32 secret.
        Returned secret contains no '=' padding.
        """
        raw = secrets.token_bytes(num_bytes)
        enc = base64.b32encode(raw).decode("ascii")
        return enc.rstrip("=")

    @staticmethod
    def _hotp_from_bytes(key: bytes, counter: int, digits: int = 6) -> str:
        """
        RFC-4226 HOTP implementation using HMAC-SHA1.
        """
        msg = counter.to_bytes(8, "big")
        digest = hmac.new(key, msg, hashlib.sha1).digest()
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
        """
        Generate TOTP code for current time or custom timestamp.
        """
        ts = int(at_time if at_time is not None else time.time())
        counter = ts // period
        key = _base32_decode(secret)
        return TOTPService._hotp_from_bytes(key, counter, digits)

    @staticmethod
    def verify(
        secret: str,
        code: str,
        tolerance: int = 1,
        period: int = 30,
        digits: int = 6,
    ) -> bool:
        """
        Verify TOTP with ±tolerance window.

        Returns:
            True  — valid token
            False — invalid or malformed

        Never raises; logs safe diagnostic info only.
        """
        try:
            code_str = str(code).strip().zfill(digits)
            key = _base32_decode(secret)
            counter = int(time.time()) // period

            for offset in range(-tolerance, tolerance + 1):
                expected = TOTPService._hotp_from_bytes(key, counter + offset, digits)
                if hmac.compare_digest(expected, code_str):
                    return True

            return False

        except Exception:
            logger.exception("TOTP verification error (invalid secret or input).")
            return False


# =====================================================================
# MFA POLICY (from SiteSettings)
# =====================================================================
class MFAEnforcer:
    """
    Read-only MFA policy provider.

    Reads from SiteSettings:
        require_mfa
        mfa_totp_issuer
        site_name

    Never raises — always returns safe values.
    """

    @staticmethod
    def required() -> bool:
        """Return True if MFA is globally required."""
        try:
            settings_obj = SiteSettings.get_solo()
            return bool(getattr(settings_obj, "require_mfa", False))
        except Exception:
            logger.warning("Failed to read require_mfa; defaulting to False.")
            return False

    @staticmethod
    def issuer() -> str:
        """
        Return MFA issuer string with safe fallbacks:
            1) mfa_totp_issuer
            2) generic default (to avoid branding leakage)
        """
        try:
            s = SiteSettings.get_solo()
            return getattr(s, "mfa_totp_issuer", None) or DEFAULT_ISSUER
        except Exception:
            logger.warning("Failed to read MFA issuer; using default.")
            return DEFAULT_ISSUER

    @staticmethod
    def provisioning_uri(
        secret: str,
        user_email: str,
        label: str | None = None,
        digits: int = 6,
        period: int = 30,
        issuer: str | None = None,
    ) -> str:
        """
        Build otpauth:// URI for QR provisioning.

        Example:
            otpauth://totp/Issuer:email?secret=ABC123&issuer=Issuer&digits=6&period=30
        """
        try:
            actual_issuer = issuer or MFAEnforcer.issuer()

            if label:
                full_label = f"{actual_issuer}:{label}"
            else:
                full_label = f"{actual_issuer}:{user_email}"

            label_encoded = quote_plus(full_label)
            issuer_encoded = quote_plus(actual_issuer)
            secret_str = _base32_pad(secret).replace("=", "")

            params = (
                f"secret={secret_str}"
                f"&issuer={issuer_encoded}"
                f"&algorithm=SHA1"
                f"&digits={digits}"
                f"&period={period}"
            )

            return f"otpauth://totp/{label_encoded}?{params}"

        except Exception:
            logger.exception("Failed to build provisioning URI.")
            raise


# =====================================================================
# OPTIONAL SECRET STORAGE HELPERS
# =====================================================================
def hmac_store_secret(secret: str, pepper: str) -> str:
    """
    Hash a secret using server-side pepper. Store the resulting digest in DB.
    """
    if not pepper:
        raise ValueError("pepper is required.")
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def compare_hmac_secret(stored_hmac: str, candidate_secret: str, pepper: str) -> bool:
    """
    Validate candidate secret against stored HMAC using constant-time comparison.
    """
    if not pepper:
        logger.warning("compare_hmac_secret() called without pepper.")
        return False
    try:
        candidate = hmac.new(
            pepper.encode(), candidate_secret.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(stored_hmac, candidate)
    except Exception:
        logger.exception("compare_hmac_secret() error.")
        return False


__all__ = ["MFAEnforcer", "TOTPService", "compare_hmac_secret", "hmac_store_secret"]
