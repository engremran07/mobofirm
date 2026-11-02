import base64

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _load_fernet_key() -> bytes:
    key = getattr(settings, "FERNET_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "FERNET_KEY must be set (urlsafe base64 32-byte key)"
        )
    try:
        raw = base64.urlsafe_b64decode(key)
    except Exception as exc:  # pragma: no cover - defensive
        raise ImproperlyConfigured("FERNET_KEY must be urlsafe base64 encoded") from exc
    if len(raw) < 32:
        raise ImproperlyConfigured("FERNET_KEY must decode to at least 32 bytes")
    return base64.urlsafe_b64encode(raw[:32])


def _fernet():
    return Fernet(_load_fernet_key())


def encrypt_password(pw: str) -> str:
    return _fernet().encrypt(pw.encode()).decode()


def decrypt_password(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
