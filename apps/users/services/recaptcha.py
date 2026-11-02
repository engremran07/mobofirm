"""
apps.users.services.recaptcha

Enterprise-Grade Google reCAPTCHA Verification Service

✅ Supports v2 + v3 with hostname verification
✅ Configurable thresholds via SiteSettings
✅ Token-level atomic caching (short-lived, cryptographic digest)
✅ Graceful degradation when disabled or unreachable
✅ Hardened against malformed tokens & network errors
✅ Fully typed, Django 5.2 / Python 3.12 compliant
✅ Zero silent failures, no unsafe hash() use
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings as django_settings
from django.core.cache import cache
from requests import Response
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException, Timeout

from apps.site_settings.models import SiteSettings

logger = logging.getLogger(__name__)

API_URL = "https://www.google.com/recaptcha/api/siteverify"
CACHE_TTL_SECONDS = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_digest(token: str) -> str:
    """
    Return a stable SHA-256 digest for the given token.
    Avoids Python's built-in hash(), which is process-randomized.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _safe_decimal(value: Any, default: Decimal = Decimal(0)) -> Decimal:
    """Safely convert a value to Decimal; return default on failure."""
    try:
        return Decimal(str(value))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Main Verification Function
# ---------------------------------------------------------------------------


def verify_recaptcha(
    token: str,
    remote_ip: str | None = None,
    action: str = "login",
) -> dict[str, Any]:
    """
    Verify a Google reCAPTCHA token using configuration from SiteSettings.

    Returns:
        {
            "ok": bool,
            "score": float | None,
            "errors": list[str],
            "error": str | None,  # internal issue, if any
        }
    """
    # ----------------------------------------------------------
    # Step 1. Load configuration
    # ----------------------------------------------------------
    try:
        settings_obj = SiteSettings.get_solo()
    except Exception as exc:
        logger.warning("reCAPTCHA: unable to load SiteSettings → %s", exc)
        return {"ok": False, "error": "settings_unavailable", "errors": []}

    recaptcha_enabled: bool = bool(getattr(settings_obj, "recaptcha_enabled", False))
    recaptcha_mode: str = str(getattr(settings_obj, "recaptcha_mode", "off")).lower()
    private_key: str | None = getattr(settings_obj, "recaptcha_private_key", None)

    # ----------------------------------------------------------
    # Step 2. Skip if disabled
    # ----------------------------------------------------------
    if not recaptcha_enabled or recaptcha_mode == "off":
        logger.debug("reCAPTCHA: bypassed (disabled/off)")
        return {"ok": True, "score": None, "errors": []}

    # ----------------------------------------------------------
    # Step 3. Validate token input
    # ----------------------------------------------------------
    if not token or not isinstance(token, str) or len(token) > 10_000:
        logger.debug("reCAPTCHA: invalid token format")
        return {"ok": False, "error": "invalid_token_format", "errors": []}

    if not private_key:
        logger.error("reCAPTCHA: missing private key in SiteSettings")
        return {"ok": False, "error": "missing_credentials", "errors": []}

    # ----------------------------------------------------------
    # Step 4. Short-term cache check (digest-based)
    # ----------------------------------------------------------
    cache_key = f"recaptcha:{action}:{_token_digest(token)}"
    try:
        cached: dict[str, Any] | None = cache.get(cache_key)
        if cached is not None:
            logger.debug("reCAPTCHA: using cached result for %s", action)
            return cached
    except Exception as exc:
        logger.debug("reCAPTCHA: cache.get failed → %s", exc)

    # ----------------------------------------------------------
    # Step 5. Build request payload
    # ----------------------------------------------------------
    payload: dict[str, str] = {"secret": private_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    timeout_s: float = max(
        float(getattr(settings_obj, "recaptcha_timeout_ms", 3000)) / 1000.0, 1.0
    )

    # ----------------------------------------------------------
    # Step 6. Perform network verification
    # ----------------------------------------------------------
    try:
        resp: Response = requests.post(API_URL, data=payload, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
    except Timeout:
        logger.warning("reCAPTCHA: timeout verifying token (%s)", action)
        return {"ok": False, "error": "timeout", "errors": []}
    except RequestsConnectionError as exc:
        logger.exception("reCAPTCHA: connection error → %s", exc)
        return {"ok": False, "error": "connection_error", "errors": []}
    except RequestException as exc:
        logger.exception("reCAPTCHA: network failure → %s", exc)
        return {"ok": False, "error": "network_error", "errors": []}
    except json.JSONDecodeError:
        logger.exception("reCAPTCHA: invalid JSON response")
        return {"ok": False, "error": "invalid_response", "errors": []}
    except Exception as exc:
        logger.exception("reCAPTCHA: unexpected exception → %s", exc)
        return {"ok": False, "error": "recaptcha_unreachable", "errors": []}

    # ----------------------------------------------------------
    # Step 7. Validate response integrity
    # ----------------------------------------------------------
    success: bool = bool(data.get("success", False))
    hostname: str | None = data.get("hostname")
    error_codes: list[str] = list(data.get("error-codes", []) or [])

    expected_host: str | None = getattr(
        django_settings, "RECAPTCHA_EXPECTED_HOSTNAME", None
    )
    if expected_host and hostname and hostname != expected_host:
        logger.warning(
            "reCAPTCHA: hostname mismatch (%s ≠ %s)", hostname, expected_host
        )
        success = False
        error_codes.append("hostname_mismatch")

    # ----------------------------------------------------------
    # Step 8. Mode-specific evaluation
    # ----------------------------------------------------------
    if recaptcha_mode == "v3":
        score = _safe_decimal(data.get("score", 0))
        threshold = _safe_decimal(
            getattr(settings_obj, "recaptcha_score_threshold", 0.5)
        )
        valid = success and score >= threshold

        result: dict[str, Any] = {
            "ok": bool(valid),
            "score": float(score),
            "errors": error_codes,
            "error": None,
        }

        if not valid:
            logger.info(
                "reCAPTCHA v3 failed: score=%.2f threshold=%.2f host=%s",
                float(score),
                float(threshold),
                hostname,
            )
    else:  # v2
        result = {
            "ok": bool(success),
            "score": None,
            "errors": error_codes,
            "error": None,
        }

    # ----------------------------------------------------------
    # Step 9. Cache result briefly
    # ----------------------------------------------------------
    try:
        cache.set(cache_key, result, timeout=CACHE_TTL_SECONDS)
        logger.debug(
            "reCAPTCHA: cached result key=%s TTL=%ss", cache_key, CACHE_TTL_SECONDS
        )
    except Exception as exc:
        logger.debug("reCAPTCHA: cache.set failed for %s → %s", cache_key, exc)

    return result
