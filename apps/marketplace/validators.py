"""URL validation for seller-supplied links (https only, no credentials)."""

from __future__ import annotations

import ipaddress
from typing import Final
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

MAX_URL_LENGTH: Final[int] = 300

# Hosts a link must belong to when its platform is known; "other" accepts any
# public https host.
PLATFORM_HOSTS: Final[dict[str, tuple[str, ...]]] = {
    "youtube": ("youtube.com", "youtu.be"),
    "telegram": ("t.me", "telegram.me", "telegram.org"),
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "x": ("x.com", "twitter.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "github": ("github.com",),
    "xda": ("xda-developers.com", "xdaforums.com"),
}


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def validate_https_url(value: str, platform: str = "other") -> str:
    """Return a cleaned https URL or raise ``ValidationError``.

    Rejects other schemes (``javascript:``, ``data:``, plain ``http:``), embedded
    credentials, IP literals, single-label hosts and, for a known platform, hosts
    that do not belong to it.
    """
    url = (value or "").strip()
    if not url:
        raise ValidationError("Enter a link.")
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError(f"Links can be at most {MAX_URL_LENGTH} characters.")
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValidationError("Links cannot contain spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValidationError("Enter a valid link.") from exc
    if parts.scheme.lower() != "https":
        raise ValidationError("Links must start with https://.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValidationError("Links cannot contain a username or password.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host or _is_ip_literal(host) or host == "localhost":
        raise ValidationError("Enter a link to a public website.")
    if port not in (None, 443):
        raise ValidationError("Links cannot use a custom port.")
    domains = PLATFORM_HOSTS.get(platform)
    if domains and not _host_matches(host, domains):
        raise ValidationError(f"Use a link on {domains[0]} for this platform.")
    return url


def validate_https_url_field(value: str) -> None:
    """Model/form field validator wrapper around :func:`validate_https_url`."""
    validate_https_url(value)
