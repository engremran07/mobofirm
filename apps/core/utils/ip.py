from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest


def _valid_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return ""


def get_client_ip(request: HttpRequest) -> str:
    """
    Resolve the client IP without trusting client-controlled headers.

    ``REMOTE_ADDR`` is used unless ``TRUSTED_PROXY_COUNT`` is positive and the
    peer is an allowed proxy. In that case the address appended by the nearest
    trusted proxy (counted from the right of ``X-Forwarded-For``) is used.
    ``X-Real-IP`` and the left-most ``X-Forwarded-For`` entry are never trusted.
    Returns an empty string when the address is unknown or malformed.
    """
    remote_ip = _valid_ip(request.META.get("REMOTE_ADDR") or "")

    try:
        trusted_hops = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    except (TypeError, ValueError):
        trusted_hops = 0
    trusted_peers = set(getattr(settings, "TRUSTED_PROXIES", ()) or ())

    if trusted_hops <= 0 or (trusted_peers and remote_ip not in trusted_peers):
        return remote_ip

    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")
    parts = [part.strip() for part in forwarded if part.strip()]
    if len(parts) < trusted_hops:
        return remote_ip
    return _valid_ip(parts[-trusted_hops]) or remote_ip
