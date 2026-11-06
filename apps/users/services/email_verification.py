from __future__ import annotations

import secrets
import string
import time
from typing import Any

from allauth.account.models import EmailAddress
from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpRequest

from apps.site_settings.services import get_site_settings

SESSION_KEY = "users_email_verification"
CODE_TTL_SECONDS = 15 * 60
MAX_CODE_ATTEMPTS = 5
# No 0/O or 1/I: codes are typed by hand from an email.
ALPHANUMERIC_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_verification_code() -> str:
    """A random code whose length and alphabet come from the site settings."""
    snapshot = get_site_settings()
    alphabet = (
        string.digits
        if snapshot.email_verification_code_type == "numeric"
        else ALPHANUMERIC_CODE_ALPHABET
    )
    return "".join(
        secrets.choice(alphabet) for _ in range(snapshot.email_verification_code_length)
    )


def issue_email_verification_code(
    request: HttpRequest, email_address: EmailAddress
) -> str:
    code = generate_verification_code()
    request.session[SESSION_KEY] = {
        "email_address_id": email_address.pk,
        "code_hash": make_password(code),
        "expires_at": int(time.time()) + CODE_TTL_SECONDS,
        "attempts": 0,
    }
    return code


def get_pending_email_address(request: HttpRequest) -> EmailAddress | None:
    state = request.session.get(SESSION_KEY)
    if not isinstance(state, dict):
        return None
    try:
        email_address_id = int(state["email_address_id"])
    except (KeyError, TypeError, ValueError):
        request.session.pop(SESSION_KEY, None)
        return None
    return EmailAddress.objects.filter(pk=email_address_id).first()


def consume_email_verification_code(
    request: HttpRequest, code: str
) -> tuple[str, EmailAddress | None]:
    state: Any = request.session.get(SESSION_KEY)
    if not isinstance(state, dict):
        return "invalid", None

    email_address = get_pending_email_address(request)
    if email_address is None:
        request.session.pop(SESSION_KEY, None)
        return "invalid", None
    if email_address.verified:
        request.session.pop(SESSION_KEY, None)
        return "already_verified", email_address

    if state.get("expired"):
        return "expired", None
    if state.get("rate_limited"):
        return "rate_limited", None

    try:
        expires_at = int(state["expires_at"])
        attempts = int(state["attempts"])
        encoded_code = state["code_hash"]
    except (KeyError, TypeError, ValueError):
        request.session[SESSION_KEY] = {
            "email_address_id": email_address.pk,
            "expired": True,
        }
        return "expired", None

    if expires_at <= int(time.time()):
        request.session[SESSION_KEY] = {
            "email_address_id": email_address.pk,
            "expired": True,
        }
        return "expired", None
    if attempts >= MAX_CODE_ATTEMPTS:
        request.session[SESSION_KEY] = {
            "email_address_id": email_address.pk,
            "rate_limited": True,
        }
        return "rate_limited", None
    if not isinstance(encoded_code, str) or not check_password(
        (code or "").strip().upper(), encoded_code
    ):
        attempts += 1
        if attempts >= MAX_CODE_ATTEMPTS:
            request.session[SESSION_KEY] = {
                "email_address_id": email_address.pk,
                "rate_limited": True,
            }
            return "rate_limited", None
        state["attempts"] = attempts
        request.session[SESSION_KEY] = state
        return "invalid", None

    return "verified", email_address
