"""Download gating service — server-side enforcement for firmware downloads.

The countdown, ad gate, single-use token, daily quota and signed local links are all
enforced here; anything the page shows (timers, labels) is informational only.

Integrity rules (F-039, F-040):

* a token belongs to one principal, a signed-in user or a signed browser cookie
  (:mod:`apps.firmwares.download_identity`), and every step (ad gate, start, link) is
  refused for anyone else;
* the signed serve link is bound to the token, the firmware, that principal and a
  short expiry, and it opens a bounded number of times
  (:mod:`apps.firmwares.download_leases`), so a leaked or replayed link is useless;
* the package's concurrent-download cap is enforced at admission under a lock on the
  user row and measures live transfer leases, not statuses;
* the ad gate never trusts the client: its proof is a server timer or a verified
  rewarded-ad unlock, and the proof used is stored on the gate log.
"""

from __future__ import annotations

import logging
import math
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.core.utils.ip import get_client_ip as get_request_ip
from apps.site_settings.services import get_site_settings

from .access import (
    check_download_access,
    concurrency_refusal,
    has_ad_unlock,
    has_perk,
)
from .download_identity import (
    DownloadIdentity,
    owns_token,
    resolve_identity,
    subject_of,
)
from .download_leases import open_lease
from .models import (
    AdGateLog,
    DownloadSession,
    DownloadToken,
    HotlinkBlock,
)
from .references import content_type_for
from .tracking_models import FirmwareDownloadAttempt

logger = logging.getLogger(__name__)

TOKEN_SALT = "firmwares.download-token"
LINK_SALT = "firmwares.download-link"
# The admin-set link TTL is an upper bound clamped to this: the link is opened the
# moment the browser follows the redirect, so a longer life only helps a thief.
LINK_TTL_CAP_SECONDS = 300
# Anonymous tokens a single IP may create per hour (an anonymous visitor without a
# cookie gets a new identity per request, so this bounds token-table inflation).
ANON_TOKENS_PER_IP_HOUR = 40
# What the server records as the kind of sponsor step; the client never chooses it.
SPONSOR_AD_TYPE = "banner"
MAX_LOGGED_SECONDS = 32767


class TokenError(Exception):
    """A download token could not be used; ``code`` selects the user message."""

    def __init__(self, code: str, wait_seconds: int = 0, message: str = ""):
        super().__init__(code)
        self.code = code
        self.wait_seconds = wait_seconds
        self.message = message


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
        "link_ttl_seconds": policy.link_ttl_seconds,
    }


def link_ttl_seconds(config: dict[str, Any] | None = None) -> int:
    """Lifetime of a signed serve link: the admin value, never above the cap."""
    config = config or _get_download_config()
    return min(int(config["link_ttl_seconds"]), LINK_TTL_CAP_SECONDS)


def _ad_gate_required(user: Any, firmware: Any, config: dict[str, Any]) -> bool:
    """Whether the sponsor step applies to this visitor and file.

    It is skipped for ad-free packages and when the user already holds a live
    rewarded-ad unlock for this very file (the ads app proved those watches).
    """
    if not (config["gate_enabled"] and config["ad_gate_enabled"]):
        return False
    return not (has_perk(user, "ad_free") or has_ad_unlock(user, firmware))


def _live_tokens(
    firmware: Any, identity: DownloadIdentity, now: datetime
) -> QuerySet[DownloadToken]:
    live = DownloadToken.objects.for_firmware(firmware).filter(
        status=DownloadToken.Status.ACTIVE, expires_at__gt=now
    )
    if identity.user is not None:
        return live.filter(user=identity.user)
    return live.filter(user__isnull=True, anon_hash=identity.anon_hash)


def create_download_token(
    firmware: Any, request: HttpRequest, identity: DownloadIdentity | None = None
) -> DownloadToken:
    """Return the caller's live token for a firmware, creating one when needed.

    Reusing an unexpired, unused token keeps repeated page loads from inflating the
    token table and keeps the countdown anchored to the first visit. The token is
    keyed by the principal (user or signed cookie), never by IP. Two parallel first
    visits converge on the oldest live token. Raises :class:`TokenError` (``rate``)
    when one IP mints too many anonymous tokens.
    """
    config = _get_download_config()
    identity = identity or resolve_identity(request)
    ip = get_request_ip(request) or None
    now = timezone.now()

    existing = (
        _live_tokens(firmware, identity, now).order_by("created_at", "pk").first()
    )
    if existing is not None:
        return existing
    if identity.user is None and ip is not None:
        recent = DownloadToken.objects.filter(
            user__isnull=True, ip=ip, created_at__gte=now - timedelta(hours=1)
        ).count()
        if recent >= ANON_TOKENS_PER_IP_HOUR:
            raise TokenError("rate")

    ad_required = _ad_gate_required(identity.user, firmware, config)
    token = DownloadToken.objects.create(
        firmware=firmware,
        user=identity.user,
        token=secrets.token_urlsafe(32),
        ad_gate_required=ad_required,
        ad_gate_completed=not ad_required,
        ip=ip,
        anon_hash=identity.anon_hash,
        status=DownloadToken.Status.ACTIVE,
        expires_at=now + timedelta(minutes=config["token_expiry_minutes"]),
    )
    winner = _live_tokens(firmware, identity, now).order_by("created_at", "pk").first()
    if winner is not None and winner.pk != token.pk:
        token.delete()
        return winner
    return token


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
    """Whole seconds of countdown left (0 when the gate is off or the wait is over)."""
    config = _get_download_config()
    if not config["gate_enabled"]:
        return 0
    return seconds_until(token, config["countdown_seconds"])


def complete_ad_gate(token: DownloadToken) -> bool:
    """Complete the sponsor step once the server's own timer has really elapsed.

    Nothing here comes from the client: the kind of ad is fixed by the server and the
    proof is recorded as ``dwell`` (a delay and an attention signal, not an
    impression). The first completion is a compare-and-swap, so a duplicate request
    logs nothing twice.
    """
    if not token.ad_gate_required:
        return True

    required_seconds = _get_download_config()["ad_gate_seconds"]
    if seconds_until(token, required_seconds) > 0:
        logger.warning("Ad-gate completed too early for token %s", token.pk)
        return False

    flipped = DownloadToken.objects.filter(pk=token.pk, ad_gate_completed=False).update(
        ad_gate_completed=True
    )
    token.ad_gate_completed = True
    if flipped:
        elapsed = int((timezone.now() - token.created_at).total_seconds())
        AdGateLog.objects.create(
            token=token,
            ad_type=SPONSOR_AD_TYPE,
            proof=AdGateLog.Proof.DWELL,
            watched_seconds=min(max(elapsed, 0), MAX_LOGGED_SECONDS),
            required_seconds=required_seconds,
            completed=True,
        )
    return True


def check_download_allowed(request: HttpRequest, firmware: Any) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for login and daily-quota policy.

    The quota counts consumed tokens (actual downloads), per user or, for
    anonymous visitors, per client IP (the IP only feeds this quota, it never
    identifies a token).
    """
    config = _get_download_config()

    if config["require_login"] and not request.user.is_authenticated:
        return False, "Login required to download firmware."

    decision = check_download_access(request.user, firmware)
    if not decision.allowed:
        return False, decision.reason

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

    return True, ""


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


def _binding_error(
    token: DownloadToken | None, firmware: Any, identity: DownloadIdentity
) -> TokenError | None:
    """Whether the token exists, is fresh, is for this file and is the caller's."""
    if token is None:
        return TokenError("expired")
    if token.expires_at < timezone.now():
        token.status = DownloadToken.Status.EXPIRED
        token.save(update_fields=["status"])
        return TokenError("expired")
    if (
        token.content_type_id != content_type_for(firmware).pk
        or token.object_id != firmware.pk
    ):
        return TokenError("invalid")
    if not owns_token(identity, token):
        logger.warning("Download token %s used by another principal", token.pk)
        return TokenError("invalid")
    return None


def _admission_error(
    token: DownloadToken | None,
    firmware: Any,
    identity: DownloadIdentity,
    config: dict[str, Any],
) -> TokenError | None:
    """The reason a token cannot be consumed now (``None`` = it can)."""
    error = _binding_error(token, firmware, identity)
    if error is not None or token is None:
        return error
    wait = (
        seconds_until(token, config["countdown_seconds"])
        if config["gate_enabled"]
        else 0
    )
    if wait > 0:
        return TokenError("countdown", wait)
    if token.ad_gate_required and not token.ad_gate_completed:
        return TokenError("adgate")
    reason = (
        concurrency_refusal(token.user)
        if token.user_id is not None and not firmware.is_external
        else ""
    )
    return TokenError("concurrent", message=reason) if reason else None


def consume_download_token(
    token_str: str,
    firmware: Any,
    request: HttpRequest,
    identity: DownloadIdentity | None = None,
) -> DownloadToken:
    """Atomically validate and consume a token, admitting a download (a lease).

    Raises :class:`TokenError` with code ``expired``, ``invalid``, ``countdown``,
    ``adgate`` or ``concurrent``. Row locking prevents two concurrent requests from
    both succeeding; the user row is locked while the package's concurrent-transfer
    cap is checked, so N+1 parallel admissions cannot all pass an N-slot cap.
    """
    config = _get_download_config()
    identity = identity or resolve_identity(request)
    content_type = content_type_for(firmware)
    error: TokenError | None = None
    with transaction.atomic():
        token = (
            DownloadToken.objects.select_for_update()
            .filter(token=token_str, status=DownloadToken.Status.ACTIVE)
            .first()
        )
        if token is not None and token.user_id is not None:
            get_user_model().objects.select_for_update().filter(
                pk=token.user_id
            ).first()
        error = _admission_error(token, firmware, identity, config)
        if error is None and token is not None:
            ip = get_request_ip(request) or None
            session = DownloadSession.objects.create(
                token=token,
                user=token.user,
                ip=ip,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
                status=DownloadSession.Status.STARTED,
            )
            open_lease(
                session,
                link_ttl=timedelta(seconds=link_ttl_seconds(config)),
                external=bool(firmware.is_external),
            )
            AdGateLog.objects.filter(token=token, session__isnull=True).update(
                session=session
            )
            FirmwareDownloadAttempt.objects.create(
                content_type=content_type,
                object_id=firmware.pk,
                user=token.user,
                session=session,
                status="downloading" if firmware.is_external else "ready",
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
    """Whether ``signature`` is the page signature this server made for the token."""
    return constant_time_compare(sign_token(token_str), signature or "")


def _link_signature(
    token_str: str, firmware_id: str, subject: str, expires: int
) -> str:
    return salted_hmac(
        LINK_SALT, f"{token_str}:{firmware_id}:{subject}:{expires}", algorithm="sha256"
    ).hexdigest()


def generate_signed_url(firmware: Any, token: DownloadToken) -> str | None:
    """Short-lived URL bound to the token, the firmware and the token's principal."""
    if firmware.is_external or not firmware.stored_file_path:
        return None
    expires = int(timezone.now().timestamp()) + link_ttl_seconds()
    sig = _link_signature(token.token, str(firmware.pk), subject_of(token), expires)
    path = reverse(
        "firmwares:serve_local_file", kwargs={"firmware_id": str(firmware.pk)}
    )
    return f"{path}?token={token.token}&exp={expires}&sig={sig}"


def verify_link(
    token_str: str, firmware_id: str, expires: str, sig: str, *, subject: str
) -> bool:
    """Validate a signed serve link: signature, firmware and principal, and expiry."""
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_at < int(timezone.now().timestamp()):
        return False
    return constant_time_compare(
        _link_signature(token_str, str(firmware_id), subject, expires_at), sig or ""
    )


def expire_stale_tokens() -> int:
    """Expire tokens that have passed their expiry time. Returns count expired."""
    return DownloadToken.objects.filter(
        status=DownloadToken.Status.ACTIVE, expires_at__lt=timezone.now()
    ).update(status=DownloadToken.Status.EXPIRED)
