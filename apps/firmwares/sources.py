"""Validation for external firmware download URLs."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

MAX_URL_LENGTH = 500


def allowed_external_hosts() -> tuple[str, ...]:
    configured = getattr(settings, "FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS", ())
    return tuple(str(host).strip().lower() for host in configured if str(host).strip())


def _host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    for entry in allowed:
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and host != entry[2:]:
                return True
        elif host == entry:
            return True
    return False


def validate_external_url(url: str) -> str:
    """Return the URL when it is https on an allowlisted host, else raise."""
    value = (url or "").strip()
    if not value or len(value) > MAX_URL_LENGTH:
        raise ValidationError(
            "An external download URL of up to 500 characters is required."
        )
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValidationError("External download URLs must use https.")
    if parts.username or parts.password:
        raise ValidationError("External download URLs must not embed credentials.")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValidationError("External download URL has no host.")
    if not _host_allowed(host, allowed_external_hosts()):
        raise ValidationError(
            "External downloads are not allowed from this host. "
            "Add it to FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS to permit it."
        )
    return value
