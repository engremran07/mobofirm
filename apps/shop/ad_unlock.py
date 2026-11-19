"""Unlock a file by watching rewarded ads: the shop's rules and ticket state machine.

Business rule (configured per package in the package builder, tab "Ads & unlocks"): an
account that is over its allowance may be offered to unlock ONE free file by watching N
rewarded ads, and an account without featured access to unlock ONE featured file by
watching X. Ad-free packages never see an offer.

``apps.ads`` proves every watch (server-validated start, heartbeat, complete) and calls
this module through ``apps.ads.unlock_bridge``:

* :func:`check_watch` runs before a watch starts and refuses it (cooldown between the
  ads of one unlock, daily cap, closed or foreign ticket, package change);
* :func:`record_watch` runs in the completion transaction and advances the ticket
  exactly once per view (``AdUnlockProgress.view_id`` is unique);
* when the last ad is counted the ticket is ``ready``: :func:`has_live_unlock` makes the
  entitlement check allow that one file, and :func:`redeem` (called when the download is
  charged) spends the ticket so it can never be used twice.

An unlock consumes no allowance. It expires :data:`READY_TTL` after the last ad and an
unfinished one after :data:`OPEN_TTL`. No online payment is involved anywhere.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.app_service import AppService

from .entitlements import (
    check_quota,
    effective_entitlements,
    file_links,
    file_ref,
    file_type_key,
    has_purchased,
)
from .models import PackageTier
from .services import lock_user
from .unlock_models import AdUnlockProgress, AdUnlockTicket

logger = logging.getLogger(__name__)

READY_TTL = timedelta(minutes=60)
OPEN_TTL = timedelta(hours=24)
DAY = timedelta(hours=24)
REFERENCE_PREFIX = "unlock:"
UNLOCK_REWARD_TYPE = "download_unlock"

# Offer states the file page shows.
NONE = "none"
QUOTA_OK = "quota_ok"
LOGIN = "login"
VERIFY = "verify"
UNAVAILABLE = "unavailable"
AVAILABLE = "available"
IN_PROGRESS = "in_progress"
COOLDOWN = "cooldown"
DAILY_CAP = "daily_cap"
READY = "ready"


class UnlockRefused(Exception):
    """A rule refused the action; ``code`` is stable and ``message`` is user-facing."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 409,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class Rule:
    """What the user's current package allows for one kind of file."""

    enabled: bool
    required: int = 0
    cooldown_seconds: int = 0
    daily_cap: int = 0
    package: PackageTier | None = None


@dataclass(frozen=True)
class UnlockOffer:
    """The state-aware panel on a file page: what the user can do about a locked file."""

    state: str
    kind: str = ""
    required: int = 0
    progress: int = 0
    message: str = ""
    cooldown_seconds: int = 0
    expires_at: datetime | None = None
    watch_url: str = ""
    daily_left: int | None = None

    @property
    def visible(self) -> bool:
        return self.state not in {NONE, QUOTA_OK}

    @property
    def can_watch(self) -> bool:
        """Whether the next ad can be watched right now (a started, ready ticket)."""
        return self.state == IN_PROGRESS


@dataclass(frozen=True)
class WatchOutcome:
    """What a counted (or refused) watch did to its ticket."""

    counted: bool
    message: str
    ready: bool = False


def kind_of(firmware: Any) -> str:
    """``free`` or ``featured`` for files that can be unlocked by ads, else ``""``."""
    tier = str(getattr(firmware, "access_tier", "") or "free")
    return tier if tier in AdUnlockTicket.Kind.values else ""


def rule_for(user: Any, kind: str) -> Rule:
    """The unlock rule of the user's package for ``kind`` (disabled when ad-free)."""
    if not getattr(user, "is_authenticated", False) or kind not in {
        AdUnlockTicket.Kind.FREE,
        AdUnlockTicket.Kind.FEATURED,
    }:
        return Rule(False)
    entitled = effective_entitlements(user)
    tier = entitled.tier
    if tier is None or entitled.perks.ad_free:
        return Rule(False)
    if kind == AdUnlockTicket.Kind.FREE:
        enabled, required = tier.unlock_offers_free, tier.unlock_ads_free
    else:
        enabled = tier.unlock_offers_featured and not entitled.can_access_featured
        required = tier.unlock_ads_featured
    if not enabled:
        return Rule(False)
    return Rule(
        True,
        required=max(required, 1),
        cooldown_seconds=tier.unlock_cooldown_seconds,
        daily_cap=tier.unlock_daily_cap,
        package=tier if tier.pk else None,
    )


def _now() -> datetime:
    return timezone.now()


def _expire(ticket: AdUnlockTicket, now: datetime) -> bool:
    """End a stale ticket (counting or unused); returns whether it was expired now."""
    live = ticket.status in {AdUnlockTicket.Status.OPEN, AdUnlockTicket.Status.READY}
    if live and ticket.expires_at is not None and ticket.expires_at <= now:
        ticket.status = AdUnlockTicket.Status.EXPIRED
        ticket.save(update_fields=["status"])
        return True
    return False


def unlocked_today(user: Any, now: datetime | None = None) -> int:
    """Files the user unlocked by ads in the last 24 hours (used or not)."""
    since = (now or _now()) - DAY
    return AdUnlockTicket.objects.filter(user=user, unlocked_at__gte=since).count()


def _ticket(
    user: Any, content_type: ContentType, object_id: Any
) -> AdUnlockTicket | None:
    return AdUnlockTicket.objects.filter(
        user=user, content_type=content_type, object_id=object_id
    ).first()


def has_live_unlock(user: Any, firmware: Any) -> bool:
    """Whether ``user`` holds an unused, unexpired unlock for exactly this file."""
    if not getattr(user, "is_authenticated", False):
        return False
    content_type, object_id = file_ref(firmware)
    return AdUnlockTicket.objects.filter(
        user=user,
        content_type=content_type,
        object_id=object_id,
        status=AdUnlockTicket.Status.READY,
        expires_at__gt=_now(),
    ).exists()


def redeem(user: Any, firmware: Any) -> bool:
    """Spend the file's ready unlock (once). ``False`` when there is nothing to spend."""
    if not getattr(user, "is_authenticated", False):
        return False
    content_type, object_id = file_ref(firmware)
    with transaction.atomic():
        ticket = (
            AdUnlockTicket.objects.select_for_update()
            .filter(user=user, content_type=content_type, object_id=object_id)
            .first()
        )
        now = _now()
        if (
            ticket is None
            or ticket.status != AdUnlockTicket.Status.READY
            or (ticket.expires_at is not None and ticket.expires_at <= now)
        ):
            if ticket is not None:
                _expire(ticket, now)
            return False
        ticket.status = AdUnlockTicket.Status.REDEEMED
        ticket.redeemed_at = now
        ticket.save(update_fields=["status", "redeemed_at"])
    logger.info("Ad unlock %s redeemed by user %s", ticket.pk, user.pk)
    return True


def _ads_offer() -> dict[str, Any]:
    """The configured rewarded ad that counts toward unlocks (``{}`` when none)."""
    get_offer = AppService.export("ads", "rewarded_offer")
    if not callable(get_offer):
        return {}
    result = get_offer(UNLOCK_REWARD_TYPE)
    return result if isinstance(result, dict) and result.get("enabled") else {}


def watch_url(ticket: AdUnlockTicket, next_url: str = "") -> str:
    """The rewarded-ad page for ``ticket`` (empty when no unlock ad is configured)."""
    base = str(_ads_offer().get("url", ""))
    if not base:
        return ""
    query = {"ref": ticket.reference}
    if next_url:
        query["next"] = next_url
    return f"{base}?{urlencode(query)}"


def _needs_unlock(user: Any, firmware: Any, kind: str) -> bool:
    """Whether the file is currently out of reach (so an unlock is worth offering)."""
    if kind == AdUnlockTicket.Kind.FEATURED:
        entitled = effective_entitlements(user)
        return not (entitled.can_access_featured or has_purchased(user, firmware))
    return not check_quota(user, firmware).allowed


def offer(user: Any, firmware: Any, *, next_url: str = "") -> UnlockOffer:
    """What the user can do about ``firmware`` right now (see the state constants)."""
    kind = kind_of(firmware)
    if not getattr(user, "is_authenticated", False) or not kind:
        return UnlockOffer(NONE)
    if has_live_unlock(user, firmware):
        ticket = _ticket(user, *file_ref(firmware))
        return UnlockOffer(
            READY,
            kind=kind,
            required=ticket.required if ticket else 0,
            progress=ticket.required if ticket else 0,
            message="Unlocked: start the download within the hour.",
            expires_at=ticket.expires_at if ticket else None,
        )
    if not _needs_unlock(user, firmware, kind):
        return UnlockOffer(QUOTA_OK)
    rule = rule_for(user, kind)
    if not rule.enabled:
        return UnlockOffer(NONE, kind=kind)
    if not getattr(user, "email_verified_at", None):
        return UnlockOffer(
            VERIFY, kind=kind, message="Verify your email to unlock files by ads."
        )
    if not _ads_offer():
        return UnlockOffer(
            UNAVAILABLE,
            kind=kind,
            required=rule.required,
            message="Unlocking by ads is not available right now.",
        )
    now = _now()
    ticket = _ticket(user, *file_ref(firmware))
    if ticket is not None:
        _expire(ticket, now)
    left = (
        max(rule.daily_cap - unlocked_today(user, now), 0) if rule.daily_cap else None
    )
    if ticket is not None and ticket.status == AdUnlockTicket.Status.OPEN:
        wait = _cooldown_left(ticket, rule, now)
        return UnlockOffer(
            COOLDOWN if wait else IN_PROGRESS,
            kind=kind,
            required=max(rule.required, ticket.progress + 1),
            progress=ticket.progress,
            cooldown_seconds=wait,
            watch_url=watch_url(ticket, next_url),
            daily_left=left,
        )
    if left == 0:
        return UnlockOffer(
            DAILY_CAP,
            kind=kind,
            required=rule.required,
            message="You reached today's unlock limit. Try again tomorrow.",
            daily_left=0,
        )
    return UnlockOffer(AVAILABLE, kind=kind, required=rule.required, daily_left=left)


def _cooldown_left(ticket: AdUnlockTicket, rule: Rule, now: datetime) -> int:
    """Whole seconds the user still has to wait before the next ad of this unlock."""
    if not rule.cooldown_seconds or ticket.last_progress_at is None:
        return 0
    gone = (now - ticket.last_progress_at).total_seconds()
    return max(0, math.ceil(rule.cooldown_seconds - gone))


def start_ticket(user: Any, firmware: Any) -> AdUnlockTicket:
    """Start (or resume) the unlock of ``firmware``; one ticket per user and file."""
    kind = kind_of(firmware)
    if not getattr(user, "is_authenticated", False) or not kind:
        raise UnlockRefused("not_offered", "This file cannot be unlocked by ads.")
    content_type, object_id = file_ref(firmware)
    with transaction.atomic():
        lock_user(user)
        state = offer(user, firmware)
        if state.state == READY:
            ticket = _ticket(user, content_type, object_id)
            if ticket is None:
                raise UnlockRefused(
                    "not_offered", "This file cannot be unlocked by ads."
                )
            return ticket
        if state.state in {IN_PROGRESS, COOLDOWN}:
            existing = _ticket(user, content_type, object_id)
            if existing is not None:
                return existing
        if state.state != AVAILABLE:
            raise UnlockRefused(
                state.state,
                state.message or "This file cannot be unlocked by ads right now.",
            )
        rule = rule_for(user, kind)
        now = _now()
        ticket = _ticket(user, content_type, object_id)
        if ticket is None:
            ticket = AdUnlockTicket.objects.create(
                user=user,
                content_type=content_type,
                object_id=object_id,
                kind=kind,
                required=rule.required,
                package=rule.package,
                expires_at=now + OPEN_TTL,
            )
        else:
            ticket.kind = kind
            ticket.status = AdUnlockTicket.Status.OPEN
            ticket.required = rule.required
            ticket.progress = 0
            ticket.cycle += 1
            ticket.package = rule.package
            ticket.created_at = now
            ticket.last_progress_at = None
            ticket.redeemed_at = None
            ticket.expires_at = now + OPEN_TTL
            ticket.save()
    logger.info(
        "Ad unlock %s started by user %s (%s ads)", ticket.pk, user.pk, rule.required
    )
    return ticket


def _parse(reference: str) -> int | None:
    if not reference.startswith(REFERENCE_PREFIX):
        return None
    raw = reference[len(REFERENCE_PREFIX) :]
    return int(raw) if raw.isdigit() else None


def check_watch(user: Any, reference: str) -> None:
    """Refuse a watch that cannot count toward ``reference`` (runs before it starts)."""
    ticket_id = _parse(reference)
    ticket = (
        AdUnlockTicket.objects.filter(pk=ticket_id, user=user).first()
        if ticket_id is not None
        else None
    )
    if ticket is None:
        raise UnlockRefused(
            "no_ticket", "Start the unlock from the file page first.", status=400
        )
    now = _now()
    if _expire(ticket, now) or ticket.status != AdUnlockTicket.Status.OPEN:
        raise UnlockRefused(
            "ticket_closed", "This unlock is over. Start a new one from the file page."
        )
    rule = rule_for(user, ticket.kind)
    if not rule.enabled:
        raise UnlockRefused(
            "not_offered", "Your package no longer offers this unlock by ads."
        )
    wait = _cooldown_left(ticket, rule, now)
    if wait:
        raise UnlockRefused(
            "cooldown",
            "Please wait a little before the next ad.",
            status=429,
            retry_after=wait,
        )
    last_ad = ticket.progress + 1 >= max(rule.required, 1)
    if last_ad and rule.daily_cap and unlocked_today(user, now) >= rule.daily_cap:
        raise UnlockRefused(
            "daily_cap",
            "You reached today's unlock limit. Try again tomorrow.",
            status=429,
        )


def record_watch(user: Any, reference: str, view_id: int) -> WatchOutcome:
    """Count one completed ad toward its ticket, exactly once per ``view_id``.

    Runs inside the ads completion transaction. A watch the rules no longer accept
    (package changed, ticket closed, cap reached) is not counted and says why; nothing
    here raises, so the ad itself still completes and is audited.
    """
    ticket_id = _parse(reference)
    if ticket_id is None:
        return WatchOutcome(False, "This ad was not linked to a file unlock.")
    with transaction.atomic():
        ticket = (
            AdUnlockTicket.objects.select_for_update()
            .filter(pk=ticket_id, user=user)
            .first()
        )
        if ticket is None:
            return WatchOutcome(False, "This ad was not linked to a file unlock.")
        known = AdUnlockProgress.objects.filter(view_id=view_id).first()
        if known is not None:
            if known.ticket_id != ticket.pk:
                return WatchOutcome(False, "That ad was already counted elsewhere.")
            return WatchOutcome(
                True,
                f"Ad {known.number} of {ticket.required} counted.",
                ticket.status == AdUnlockTicket.Status.READY,
            )
        now = _now()
        if _expire(ticket, now) or ticket.status != AdUnlockTicket.Status.OPEN:
            return WatchOutcome(False, "This unlock is over; the ad was not counted.")
        rule = rule_for(user, ticket.kind)
        if not rule.enabled:
            ticket.status = AdUnlockTicket.Status.CANCELLED
            ticket.save(update_fields=["status"])
            return WatchOutcome(
                False, "Your package no longer needs ads for this file."
            )
        if _cooldown_left(ticket, rule, now):
            return WatchOutcome(False, "That ad came too soon and was not counted.")
        required = max(rule.required, 1)
        step = min(ticket.progress + 1, required)
        last = step >= required
        if last and rule.daily_cap and unlocked_today(user, now) >= rule.daily_cap:
            return WatchOutcome(False, "Today's unlock limit is reached.")
        try:
            with transaction.atomic():
                AdUnlockProgress.objects.create(
                    ticket=ticket, view_id=view_id, cycle=ticket.cycle, number=step
                )
        except IntegrityError:
            return WatchOutcome(True, "This ad was already counted.")
        ticket.required = required
        ticket.progress = step
        ticket.last_progress_at = now
        fields = ["required", "progress", "last_progress_at"]
        if last:
            ticket.status = AdUnlockTicket.Status.READY
            ticket.unlocked_at = now
            ticket.expires_at = now + READY_TTL
            fields += ["status", "unlocked_at", "expires_at"]
        else:
            ticket.expires_at = now + OPEN_TTL
            fields.append("expires_at")
        ticket.save(update_fields=fields)
    if last:
        logger.info("Ad unlock %s ready for user %s", ticket.pk, user.pk)
        return WatchOutcome(
            True, "File unlocked. Start the download within the hour.", True
        )
    return WatchOutcome(True, f"Ad {step} of {required} counted.")


def cancel_stale() -> int:
    """Expire tickets whose time ran out (periodic housekeeping); returns the count."""
    now = _now()
    return AdUnlockTicket.objects.filter(
        status__in=[AdUnlockTicket.Status.OPEN, AdUnlockTicket.Status.READY],
        expires_at__lte=now,
    ).update(status=AdUnlockTicket.Status.EXPIRED)


def panel_urls(firmware: Any) -> dict[str, str]:
    """The shop endpoints the file page's unlock panel talks to ({} if not linkable)."""
    type_key = file_type_key(type(firmware))
    if type_key is None:
        return {}
    kwargs = {"firmware_type": type_key, "firmware_id": firmware.pk}
    return {
        "panel_url": reverse("shop:unlock_panel", kwargs=kwargs),
        "start_url": reverse("shop:unlock_start", kwargs=kwargs),
    }


def panel_context(user: Any, firmware: Any, *, back_url: str = "") -> dict[str, Any]:
    """Everything ``shop/_ad_unlock_panel.html`` shows for ``firmware`` ({} = no panel)."""
    state = offer(user, firmware, next_url=back_url)
    urls = panel_urls(firmware)
    if not state.visible or not urls:
        return {}
    content_type, object_id = file_ref(firmware)
    links = file_links(content_type.pk, object_id)
    return {
        "offer": state,
        "firmware": firmware,
        "pricing_url": reverse("shop:pricing"),
        "download_url": links[1] if links else "",
        **urls,
    }


@dataclass(frozen=True)
class UnlockAnalytics:
    """Counters for the staff view of ad unlocks (a revenue proxy, not money)."""

    started: int
    unlocked: int
    redeemed: int
    ads_counted: int

    @property
    def unlock_rate(self) -> int:
        """Percent of started unlocks that reached the last ad."""
        return round(self.unlocked / self.started * 100) if self.started else 0

    @property
    def ads_per_unlock(self) -> float:
        """Rewarded ads watched per finished unlock (the ad impressions each one earns)."""
        return round(self.ads_counted / self.unlocked, 1) if self.unlocked else 0.0

    def summary(self) -> str:
        return (
            f"{self.started} started, {self.unlocked} unlocked ({self.unlock_rate}%), "
            f"{self.redeemed} used by a download; {self.ads_counted} rewarded ads "
            f"counted ({self.ads_per_unlock} per unlock)."
        )


def analytics(*, since: datetime | None = None) -> UnlockAnalytics:
    """Unlock funnel counters since ``since`` (all time by default)."""
    tickets = AdUnlockTicket.objects.all()
    steps = AdUnlockProgress.objects.all()
    if since is not None:
        tickets = tickets.filter(created_at__gte=since)
        steps = steps.filter(counted_at__gte=since)
    return UnlockAnalytics(
        started=tickets.count(),
        unlocked=tickets.filter(unlocked_at__isnull=False).count(),
        redeemed=tickets.filter(status=AdUnlockTicket.Status.REDEEMED).count(),
        ads_counted=steps.count(),
    )
