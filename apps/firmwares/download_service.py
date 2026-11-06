"""Download gating service — server-side enforcement for firmware downloads.

The countdown, ad gate, single-use token, daily quota and signed local links are all
enforced here; anything the page shows (timers, labels) is informational only.
"""

from __future__ import annotations

import logging
import math
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.core.utils.ip import get_client_ip as get_request_ip
from apps.site_settings.services import get_site_settings

from .models import (
    AdGateLog,
    DownloadSession,
    DownloadToken,
    HotlinkBlock,
)
from .tracking_models import FirmwareDownloadAttempt

logger = logging.getLogger(__name__)

TOKEN_SALT = "firmwares.download-token"
LINK_SALT = "firmwares.download-link"


def _get_download_config() -> dict[str, Any]:
    """Load the server-side download policy from the runtime site settings."""
    policy = get_site_settings().download
    return {
        "gate_enabled": policy.gate_enabled,
        "countdown_seconds": policy.countdown_seconds,
        "ad_gate_enabled": policy.ad_gate_enabled,
        "ad_gate_seconds": policy.ad_gate_seconds,
        "token_expiry_minutes": policy.token_expiry_minutes,
        "require_login": policy.require_login,
        "max_per_day": policy.max_per_day,
        "hotlink_protection": policy.hotlink_protection,
        "link_encryption": policy.link_encryption,
        "link_ttl_seconds": policy.link_ttl_seconds,
    }


def _firmware_content_type(firmware: Any) -> ContentType:
    return ContentType.objects.get_for_model(firmware, for_concrete_model=False)


def create_download_token(firmware: Any, request: HttpRequest) -> DownloadToken:
    """Return the caller's live token for a firmware, creating one when needed.

    Reusing an unexpired, unused token keeps repeated page loads from inflating
    the token table and keeps the countdown anchored to the first visit.
    """
    config = _get_download_config()
    user = request.user if request.user.is_authenticated else None
    ip = get_request_ip(request) or None
    now = timezone.now()

    live = DownloadToken.objects.for_firmware(firmware).filter(
        status=DownloadToken.Status.ACTIVE, expires_at__gt=now
    )
    live = live.filter(user=user) if user else live.filter(user__isnull=True, ip=ip)
    existing = live.order_by("-created_at").first()
    if existing is not None:
        return existing

    gate_on = config["gate_enabled"]
    ad_required = bool(gate_on and config["ad_gate_enabled"])
    return DownloadToken.objects.create(
        firmware=firmware,
        user=user,
        token=secrets.token_urlsafe(32),
        ad_gate_required=ad_required,
        ad_gate_completed=not ad_required,
        ip=ip,
        status=DownloadToken.Status.ACTIVE,
        expires_at=now + timedelta(minutes=config["token_expiry_minutes"]),
    )


def validate_download_token(token_str: str) -> DownloadToken | None:
    """Return the active token, expiring it when stale. Read-only otherwise."""
    token = DownloadToken.objects.filter(
        token=token_str, status=DownloadToken.Status.ACTIVE
    ).first()
    if token is None:
        return None
    if token.expires_at < timezone.now():
        token.status = DownloadToken.Status.EXPIRED
        token.save(update_fields=["status"])
        return None
    return token


def seconds_until(token: DownloadToken, wait_seconds: int) -> int:
    """Whole seconds still to wait, measured from token creation."""
    elapsed = (timezone.now() - token.created_at).total_seconds()
    return max(0, math.ceil(wait_seconds - elapsed))


def countdown_remaining(token: DownloadToken) -> int:
    config = _get_download_config()
    if not config["gate_enabled"]:
        return 0
    return seconds_until(token, config["countdown_seconds"])


def complete_ad_gate(token: DownloadToken, ad_type: str = "video") -> bool:
    """Mark the ad gate complete once its minimum watch time has really elapsed."""
    if not token.ad_gate_required:
        return True

    required_seconds = _get_download_config()["ad_gate_seconds"]
    if seconds_until(token, required_seconds) > 0:
        logger.warning("Ad-gate completed too early for token %s", token.pk)
        return False

    if not token.ad_gate_completed:
        token.ad_gate_completed = True
        token.save(update_fields=["ad_gate_completed"])
        AdGateLog.objects.create(
            token=token,
            ad_type=ad_type,
            watched_seconds=required_seconds,
            required_seconds=required_seconds,
            completed=True,
        )
    return True


def check_download_allowed(request: HttpRequest, firmware: Any) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for login and daily-quota policy.

    The quota counts consumed tokens (actual downloads), per user or, for
    anonymous visitors, per client IP.
    """
    config = _get_download_config()

    if config["require_login"] and not request.user.is_authenticated:
        return False, "Login required to download firmware."

    if config["max_per_day"] > 0:
        day_ago = timezone.now() - timedelta(hours=24)
        used = DownloadToken.objects.filter(
            status=DownloadToken.Status.USED, used_at__gte=day_ago
        )
        if request.user.is_authenticated:
            used = used.filter(user=request.user)
        else:
            ip = get_request_ip(request)
            used = used.filter(user__isnull=True, ip=ip) if ip else used.none()
        if used.count() >= config["max_per_day"]:
            return False, f"Daily download limit ({config['max_per_day']}) reached."

    return check_entitlement(request, firmware)


def _entitlement_context(request: HttpRequest, firmware: Any):
    """Shop services plus the firmware type key, or ``None`` when not enforced.

    Optional seam to ``apps.shop``: off unless ``SHOP_ENTITLEMENTS_ENFORCED`` is true,
    the shop app is installed, and the visitor is signed in (anonymous visitors keep
    the IP-based daily limit above).
    """
    if not getattr(settings, "SHOP_ENTITLEMENTS_ENFORCED", False):
        return None
    if not request.user.is_authenticated or not django_apps.is_installed("apps.shop"):
        return None
    from apps.shop import services as shop_services

    from .catalog import TYPE_KEY_BY_CLASS

    type_key = TYPE_KEY_BY_CLASS.get(type(firmware), "")
    return shop_services, type_key


def _size_mb(firmware: Any) -> int:
    return math.ceil((firmware.file_size or 0) / (1024 * 1024))


def check_entitlement(request: HttpRequest, firmware: Any) -> tuple[bool, str]:
    """Apply the visitor's shop package (type access and quotas) when enforced."""
    context = _entitlement_context(request, firmware)
    if context is None:
        return True, ""
    shop_services, type_key = context
    if type_key in shop_services.TIER_GATED_TYPES and (
        not shop_services.firmware_type_allowed(request.user, type_key)
    ):
        return False, "Your package does not include this firmware category."
    decision = shop_services.check_download_quota(
        request.user, size_mb=_size_mb(firmware)
    )
    return decision.allowed, decision.reason


def record_entitlement_download(request: HttpRequest, firmware: Any) -> None:
    """Count a granted download against the visitor's package (when enforced)."""
    context = _entitlement_context(request, firmware)
    if context is not None:
        context[0].record_download(request.user, size_mb=_size_mb(firmware))


def check_hotlink(request: HttpRequest) -> bool:
    """Check if request is a blocked hotlink. Returns True if BLOCKED."""
    config = _get_download_config()
    if not config["hotlink_protection"]:
        return False

    referer = request.META.get("HTTP_REFERER", "")
    if not referer:
        return False

    try:
        domain = urlsplit(referer).hostname or ""
    except ValueError:
        return False

    allowed_hosts = getattr(settings, "ALLOWED_HOSTS", [])
    if domain in allowed_hosts or domain in {"localhost", "127.0.0.1"}:
        return False

    blocked = HotlinkBlock.objects.filter(domain=domain, is_active=True).first()
    if blocked:
        HotlinkBlock.objects.filter(pk=blocked.pk).update(
            blocked_count=blocked.blocked_count + 1
        )
        return True

    return False


class TokenError(Exception):
    """A download token could not be consumed; ``code`` selects the user message."""

    def __init__(self, code: str, wait_seconds: int = 0):
        super().__init__(code)
        self.code = code
        self.wait_seconds = wait_seconds


def consume_download_token(
    token_str: str, firmware: Any, request: HttpRequest
) -> DownloadToken:
    """Atomically validate and consume a token, opening a download session.

    Raises :class:`TokenError` with code ``expired``, ``invalid``, ``countdown`` or
    ``adgate``. Row locking prevents two concurrent requests from both succeeding.
    """
    config = _get_download_config()
    content_type = _firmware_content_type(firmware)
    error: TokenError | None = None
    with transaction.atomic():
        token = (
            DownloadToken.objects.select_for_update()
            .filter(token=token_str, status=DownloadToken.Status.ACTIVE)
            .first()
        )
        if token is None:
            error = TokenError("expired")
        elif token.expires_at < timezone.now():
            token.status = DownloadToken.Status.EXPIRED
            token.save(update_fields=["status"])
            error = TokenError("expired")
        elif token.content_type_id != content_type.pk or token.object_id != firmware.pk:
            error = TokenError("invalid")
        elif (
            config["gate_enabled"]
            and (wait := seconds_until(token, config["countdown_seconds"])) > 0
        ):
            error = TokenError("countdown", wait)
        elif token.ad_gate_required and not token.ad_gate_completed:
            error = TokenError("adgate")
        else:
            ip = get_request_ip(request) or None
            session = DownloadSession.objects.create(
                token=token,
                user=token.user,
                ip=ip,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
                status=DownloadSession.Status.STARTED,
            )
            AdGateLog.objects.filter(token=token, session__isnull=True).update(
                session=session
            )
            FirmwareDownloadAttempt.objects.create(
                content_type=content_type,
                object_id=firmware.pk,
                user=token.user,
                status="ready",
                ip_address=ip,
            )
            token.status = DownloadToken.Status.USED
            token.used_at = timezone.now()
            token.save(update_fields=["status", "used_at"])
    if error is not None or token is None:
        raise error or TokenError("expired")
    return token


def sign_token(token_str: str) -> str:
    """HMAC the token with the project secret (full SHA-256 digest)."""
    return salted_hmac(TOKEN_SALT, token_str, algorithm="sha256").hexdigest()


def verify_signature(token_str: str, signature: str) -> bool:
    return constant_time_compare(sign_token(token_str), signature or "")


def _link_signature(token_str: str, firmware_id: str, expires: int) -> str:
    return salted_hmac(
        LINK_SALT, f"{token_str}:{firmware_id}:{expires}", algorithm="sha256"
    ).hexdigest()


def generate_signed_url(firmware: Any, token: DownloadToken) -> str | None:
    """Short-lived URL bound to the token and firmware for a stored file."""
    if firmware.is_external or not firmware.stored_file_path:
        return None
    ttl = _get_download_config()["link_ttl_seconds"]
    expires = int(timezone.now().timestamp()) + ttl
    sig = _link_signature(token.token, str(firmware.pk), expires)
    path = reverse(
        "firmwares:serve_local_file", kwargs={"firmware_id": str(firmware.pk)}
    )
    return f"{path}?token={token.token}&exp={expires}&sig={sig}"


def verify_link(token_str: str, firmware_id: str, expires: str, sig: str) -> bool:
    """Validate a signed serve link: signature, firmware binding and expiry."""
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_at < int(timezone.now().timestamp()):
        return False
    return constant_time_compare(
        _link_signature(token_str, str(firmware_id), expires_at), sig or ""
    )


def expire_stale_tokens() -> int:
    """Expire tokens that have passed their expiry time. Returns count expired."""
    return DownloadToken.objects.filter(
        status=DownloadToken.Status.ACTIVE, expires_at__lt=timezone.now()
    ).update(status=DownloadToken.Status.EXPIRED)
