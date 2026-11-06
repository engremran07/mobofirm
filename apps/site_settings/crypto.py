"""Encryption for write-only credentials stored in ``SiteSettings``.

The Fernet key comes from ``SOCIAL_CREDENTIAL_ENCRYPTION_KEY``, the project's
single key for third-party credentials at rest (see ``apps.users.models_social``).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.encoding import force_bytes


class SecretUnavailableError(RuntimeError):
    """A stored secret cannot be decrypted with the configured key."""


def _fernet() -> Fernet:
    key = getattr(settings, "SOCIAL_CREDENTIAL_ENCRYPTION_KEY", "")
    if key:
        raw = key.encode("ascii") if isinstance(key, str) else key
    elif settings.DEBUG:
        digest = hashlib.sha256(force_bytes(settings.SECRET_KEY)).digest()
        raw = base64.urlsafe_b64encode(digest)
    else:
        raise ImproperlyConfigured(
            "SOCIAL_CREDENTIAL_ENCRYPTION_KEY must be configured outside DEBUG."
        )
    return Fernet(raw)


def encrypt_secret(plaintext: str) -> str:
    """Return the Fernet token for ``plaintext`` (an empty value stays empty)."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Return the plaintext for a stored token; an empty token is an empty secret."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretUnavailableError(
            "A stored site secret cannot be decrypted; re-enter it in the admin."
        ) from exc
