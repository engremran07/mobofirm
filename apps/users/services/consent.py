from __future__ import annotations

import hashlib
import hmac
from typing import Any

from django.conf import settings
from django.utils.encoding import force_bytes


def _digest(value: str) -> str:
    if not value:
        return ""
    return hmac.new(
        force_bytes(settings.SECRET_KEY),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_ip(value: str) -> str:
    return _digest(value)


def hash_ua(value: str) -> str:
    return _digest(value)


def _request_categories(request: Any) -> dict[str, Any]:
    categories = getattr(request, "consent_categories", None)
    if categories is None:
        session = getattr(request, "session", None)
        categories = session.get("consent_categories", {}) if session else {}
    if categories:
        return categories if isinstance(categories, dict) else {}

    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        from apps.users.models import ConsentDecision

        decision = (
            ConsentDecision.objects.filter(user=user).order_by("-created_at").first()
        )
        if decision and isinstance(decision.categories, dict):
            return decision.categories
    return {}


def check(category: str, request: Any) -> bool:
    return bool(_request_categories(request).get(category, False))
