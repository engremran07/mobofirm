from __future__ import annotations

import hashlib
import hmac

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
