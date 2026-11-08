"""Rewarded ads: a server-validated start → heartbeat → complete flow.

The proof of a watch is a server-controlled timer, never a client claim and never a
third-party SDK: ``start`` mints a random single-use token (only its keyed digest is
stored), ``heartbeat`` accrues the time the server actually saw the user on the page
(each beat adds at most ``HEARTBEAT_MAX_GAP_SECONDS``), and ``complete`` grants the reward
only when the accrued time reaches the minimum. Completion is idempotent and
replay-proof: a second call returns the first outcome and grants nothing.

Rewards go through the owner's contract: ``credits`` through the wallet's
``ads.rewarded_view_completed`` rule, ``device_reset`` through ``apps.devices.slots``,
``download_bonus`` and ``ad_free`` through a :class:`RewardedAdGrant` that the shop's
entitlement resolver reads via the documented modifier seam.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps as django_apps
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.ads.models import (
    AdsSettings,
    RewardedAdConfig,
    RewardedAdGrant,
    RewardedAdView,
)
from apps.consent.constants import ADVERTISING
from apps.consent.services import has_consent
from apps.core.utils.ip import get_client_ip
from apps.users.services.rate_limit import allow_action

logger = logging.getLogger(__name__)

EVENT_KEY = "ads.rewarded_view_completed"
DEFAULT_MIN_WATCH_SECONDS = 15
HEARTBEAT_INTERVAL_SECONDS = 5
HEARTBEAT_MAX_GAP_SECONDS = 15
SESSION_GRACE_SECONDS = 120
CLIENT_CLAIM_TOLERANCE_SECONDS = 3
MAX_EARLY_COMPLETIONS = 3
STARTS_PER_HOUR = 20
REJECTED_ROWS_PER_HOUR = 5

FLAG_TOO_EARLY = "completed_too_early"
FLAG_IMPOSSIBLE = "impossible_duration"
FLAG_REPLAY = "replayed_token"
FLAG_TOO_FREQUENT = "too_frequent"
FLAG_ABANDONED = "abandoned_restart"

__all__ = [
    "Analytics",
    "CompletionResult",
    "RewardedAdError",
    "StartResult",
    "analytics",
    "available_config",
    "complete",
    "digest_token",
    "effective_min_watch",
    "entitlement_modifier",
    "heartbeat",
    "is_available",
    "start",
    "unavailable_reason",
    "user_is_ad_free",
]


class RewardedAdError(Exception):
    """A rule refused the request; ``code`` is stable and ``message`` is user-facing."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        retry_after: int | None = None,
        remaining: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
        self.remaining = remaining


@dataclass(frozen=True)
class StartResult:
    view_id: int
    token: str
    min_watch_seconds: int
    heartbeat_seconds: int
    expires_in: int


@dataclass(frozen=True)
class CompletionResult:
    view_id: int
    status: str
    reward_granted: bool
    reward_type: str
    reward_amount: int
    message: str
    already_completed: bool = False


@dataclass(frozen=True)
class Analytics:
    starts: int
    completions: int
    rejected: int
    expired: int
    flagged: int
    rewards_granted: int
    by_type: dict[str, int]

    @property
    def completion_rate(self) -> int:
        return round(self.completions / self.starts * 100) if self.starts else 0


# --- configuration state ------------------------------------------------------


def effective_min_watch(config: RewardedAdConfig) -> int:
    """The watch time the server demands: the config value, never below the floor."""
    from django.conf import settings

    floor = int(getattr(settings, "REWARDED_AD_MIN_WATCH_FLOOR_SECONDS", 5))
    return max(config.min_watch_seconds or DEFAULT_MIN_WATCH_SECONDS, floor)


def _placement_ready(config: RewardedAdConfig) -> bool:
    placement = config.placement
    return bool(
        placement is not None
        and placement.is_active
        and placement.is_enabled
        and not placement.is_deleted
    )


def _reward_backend_missing(config: RewardedAdConfig) -> str:
    """Why this reward type cannot be paid out on this site, or an empty string."""
    kind = config.reward_type
    if kind == RewardedAdConfig.RewardType.CREDITS and not django_apps.is_installed(
        "apps.wallet"
    ):
        return "Credits are not available on this site."
    if (
        kind == RewardedAdConfig.RewardType.DEVICE_RESET
        and not django_apps.is_installed("apps.devices")
    ):
        return "Device slot resets are not available on this site."
    return ""


def unavailable_reason(config: RewardedAdConfig | None) -> str:
    """Why rewarded ads cannot run right now ('' when they can); shown honestly to users."""
    settings_row = AdsSettings.get_solo()
    if not settings_row.ads_enabled or not settings_row.rewarded_ads_enabled:
        return "Rewarded ads are not enabled right now."
    if config is None or config.is_deleted or not config.is_enabled:
        return "This rewarded ad is not enabled right now."
    if not _placement_ready(config):
        return "This rewarded ad is not configured yet."
    return _reward_backend_missing(config)


def is_available(config: RewardedAdConfig | None) -> bool:
    return not unavailable_reason(config)


def available_config(reward_type: str) -> RewardedAdConfig | None:
    """The first enabled, fully configured config that pays ``reward_type``."""
    candidates = RewardedAdConfig.objects.filter(
        reward_type=reward_type, is_enabled=True, is_deleted=False
    ).select_related("placement")
    for config in candidates.order_by("pk"):
        if is_available(config):
            return config
    return None


# --- eligibility --------------------------------------------------------------


def _day_start(now: datetime) -> datetime:
    return timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)


def _completed(user: Any, config: RewardedAdConfig) -> Any:
    return RewardedAdView.objects.filter(
        user=user, config=config, status=RewardedAdView.Status.COMPLETED
    )


def _check_caps(user: Any, config: RewardedAdConfig, now: datetime) -> None:
    done = _completed(user, config)
    if config.cooldown_minutes:
        last = (
            done.order_by("-completed_at")
            .values_list("completed_at", flat=True)
            .first()
        )
        if last is not None:
            wait = timedelta(minutes=config.cooldown_minutes) - (now - last)
            if wait.total_seconds() > 0:
                raise RewardedAdError(
                    "cooldown",
                    "Please wait a little before watching another one.",
                    status=429,
                    retry_after=int(wait.total_seconds()) + 1,
                )
    day_start = _day_start(now)
    if (
        config.daily_limit_per_user
        and done.filter(completed_at__gte=day_start).count()
        >= config.daily_limit_per_user
    ):
        raise RewardedAdError(
            "daily_cap",
            "You have reached today's limit for this reward. Try again tomorrow.",
            status=429,
            retry_after=int((day_start + timedelta(days=1) - now).total_seconds()),
        )
    if (
        config.monthly_limit_per_user
        and done.filter(completed_at__gte=day_start.replace(day=1)).count()
        >= config.monthly_limit_per_user
    ):
        raise RewardedAdError(
            "monthly_cap",
            "You have reached this month's limit for this reward.",
            status=429,
        )


def _record_refusal(
    request: HttpRequest, user: Any, config: RewardedAdConfig, flag: str
) -> None:
    """Keep a bounded trail of refused (too frequent) starts for the abuse dashboard."""
    if not allow_action(
        f"ads:rewarded:refusal:{user.pk}",
        max_attempts=REJECTED_ROWS_PER_HOUR,
        window_seconds=3600,
    ):
        return
    RewardedAdView.objects.create(
        user=user,
        config=config,
        status=RewardedAdView.Status.REJECTED,
        flags=[flag],
        is_flagged=True,
        ip_address=get_client_ip(request) or None,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:500],
        reward_note="Start refused: too frequent.",
    )


def check_eligibility(
    request: HttpRequest, user: Any, config: RewardedAdConfig
) -> None:
    """Raise ``RewardedAdError`` unless ``user`` may start ``config`` right now."""
    reason = unavailable_reason(config)
    if reason:
        raise RewardedAdError("not_available", reason, status=409)
    if not getattr(user, "email_verified_at", None):
        raise RewardedAdError(
            "email_unverified",
            "Verify your email address to watch rewarded ads.",
            status=403,
        )
    if not has_consent(request, ADVERTISING):
        raise RewardedAdError(
            "consent_required",
            "Rewarded ads need your advertising consent. Allow it in the privacy centre.",
            status=403,
        )
    _check_caps(user, config, timezone.now())


# --- start / heartbeat / complete ----------------------------------------------


def digest_token(token: str) -> str:
    return salted_hmac(
        "apps.ads.rewarded-token.v1", token, algorithm="sha256"
    ).hexdigest()


def _flag(view: RewardedAdView, flag: str) -> None:
    if flag not in view.flags:
        view.flags = [*view.flags, flag]
    view.is_flagged = True


def start(request: HttpRequest, user: Any, config: RewardedAdConfig) -> StartResult:
    """Begin a watch: one active view per user, a fresh single-use token."""
    if not allow_action(
        f"ads:rewarded:start:{user.pk}",
        max_attempts=STARTS_PER_HOUR,
        window_seconds=3600,
    ):
        raise RewardedAdError(
            "rate_limited", "Too many attempts. Try again later.", status=429
        )
    try:
        check_eligibility(request, user, config)
    except RewardedAdError as error:
        if error.code in {"cooldown", "daily_cap", "monthly_cap"}:
            _record_refusal(request, user, config, FLAG_TOO_FREQUENT)
        raise
    required = effective_min_watch(config)
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        for stale in RewardedAdView.objects.select_for_update().filter(
            user=user, status=RewardedAdView.Status.STARTED
        ):
            stale.status = RewardedAdView.Status.EXPIRED
            stale.token_digest = ""
            _flag(stale, FLAG_ABANDONED)
            stale.save(update_fields=["status", "token_digest", "flags", "is_flagged"])
        try:
            view = RewardedAdView.objects.create(
                user=user,
                config=config,
                token_digest=digest_token(token),
                expires_at=now + timedelta(seconds=required + SESSION_GRACE_SECONDS),
                ip_address=get_client_ip(request) or None,
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:500],
            )
        except IntegrityError as error:
            raise RewardedAdError(
                "already_active",
                "Another rewarded ad is already running for you.",
                status=409,
            ) from error
    return StartResult(
        view_id=view.pk,
        token=token,
        min_watch_seconds=required,
        heartbeat_seconds=HEARTBEAT_INTERVAL_SECONDS,
        expires_in=required + SESSION_GRACE_SECONDS,
    )


def _locked_view(user: Any, token: str) -> RewardedAdView:
    """The caller's own view for ``token``; another user's token is simply invalid."""
    if not token:
        raise RewardedAdError("invalid_token", "This ad session is not valid.")
    view = (
        RewardedAdView.objects.select_for_update(of=("self",))
        .select_related("config", "config__placement", "user")
        .filter(user=user, token_digest=digest_token(token))
        .first()
    )
    if view is None:
        raise RewardedAdError("invalid_token", "This ad session is not valid.")
    return view


def _accrue(view: RewardedAdView, now: datetime) -> None:
    since = view.last_heartbeat_at or view.started_at
    gap = max(0.0, (now - since).total_seconds())
    view.accrued_ms += int(min(gap, HEARTBEAT_MAX_GAP_SECONDS) * 1000)
    view.last_heartbeat_at = now


def _refuse_inactive(view: RewardedAdView) -> RewardedAdError:
    """The error for a heartbeat/complete on a view that is not running; flags replays."""
    if view.status == RewardedAdView.Status.COMPLETED:
        _flag(view, FLAG_REPLAY)
        view.save(update_fields=["flags", "is_flagged"])
    return RewardedAdError("not_active", "This ad session is already over.", status=409)


def _refuse_expired(view: RewardedAdView) -> RewardedAdError:
    view.status = RewardedAdView.Status.EXPIRED
    view.save(update_fields=["status"])
    return RewardedAdError(
        "expired", "This ad session expired. Start again.", status=410
    )


def heartbeat(
    user: Any, token: str, *, claimed_seconds: float | None = None
) -> dict[str, int]:
    """Record that the user is still on the ad; returns the server's own progress.

    Refusals are decided inside the transaction (so flags and expiry persist) and
    raised after it commits.
    """
    with transaction.atomic():
        view = _locked_view(user, token)
        config = view.config
        outcome: dict[str, int] | RewardedAdError
        if view.status != RewardedAdView.Status.STARTED or config is None:
            outcome = _refuse_inactive(view)
        else:
            now = timezone.now()
            if view.expires_at is not None and now > view.expires_at:
                outcome = _refuse_expired(view)
            else:
                elapsed = (now - view.started_at).total_seconds()
                if claimed_seconds is not None and (
                    claimed_seconds < 0
                    or claimed_seconds > elapsed + CLIENT_CLAIM_TOLERANCE_SECONDS
                ):
                    _flag(view, FLAG_IMPOSSIBLE)
                _accrue(view, now)
                view.heartbeat_count += 1
                view.save(
                    update_fields=[
                        "accrued_ms",
                        "last_heartbeat_at",
                        "heartbeat_count",
                        "flags",
                        "is_flagged",
                    ]
                )
                required = effective_min_watch(config)
                watched = view.accrued_ms // 1000
                outcome = {
                    "watched_seconds": watched,
                    "remaining_seconds": max(0, required - watched),
                    "required_seconds": required,
                }
    if isinstance(outcome, RewardedAdError):
        raise outcome
    return outcome


def complete(user: Any, token: str) -> CompletionResult:
    """Grant the reward once the server has seen enough watch time; idempotent."""
    with transaction.atomic():
        view = _locked_view(user, token)
        config = view.config
        outcome: CompletionResult | RewardedAdError
        if view.status == RewardedAdView.Status.COMPLETED:
            _flag(view, FLAG_REPLAY)
            view.save(update_fields=["flags", "is_flagged"])
            outcome = _result(view, already=True)
        elif view.status != RewardedAdView.Status.STARTED or config is None:
            outcome = _refuse_inactive(view)
        else:
            now = timezone.now()
            if view.expires_at is not None and now > view.expires_at:
                outcome = _refuse_expired(view)
            else:
                _accrue(view, now)
                required = effective_min_watch(config)
                elapsed = (now - view.started_at).total_seconds()
                if elapsed < required or view.accrued_ms < required * 1000:
                    outcome = _refuse_early(view, required, elapsed)
                else:
                    _grant(view, config)
                    view.status = RewardedAdView.Status.COMPLETED
                    view.completed_at = now
                    view.watch_duration_seconds = view.accrued_ms // 1000
                    view.save()
                    outcome = _result(view, already=False)
    if isinstance(outcome, RewardedAdError):
        raise outcome
    return outcome


def _refuse_early(
    view: RewardedAdView, required: int, elapsed: float
) -> RewardedAdError:
    """Too early: flag it, and reject the view after repeated attempts."""
    view.early_attempts += 1
    _flag(view, FLAG_TOO_EARLY)
    if view.early_attempts >= MAX_EARLY_COMPLETIONS:
        view.status = RewardedAdView.Status.REJECTED
        view.reward_note = "Completion requested before the minimum watch time."
    view.save()
    seen = min(elapsed, view.accrued_ms / 1000)
    return RewardedAdError(
        "not_watched_long_enough",
        "Keep watching until the timer finishes.",
        status=409,
        remaining=max(1, int(required - seen) + 1),
    )


def _result(view: RewardedAdView, *, already: bool) -> CompletionResult:
    return CompletionResult(
        view_id=view.pk,
        status=view.status,
        reward_granted=view.reward_granted,
        reward_type=view.reward_type,
        reward_amount=view.reward_amount,
        message=view.reward_note,
        already_completed=already,
    )


# --- granting -----------------------------------------------------------------


def _grant(view: RewardedAdView, config: RewardedAdConfig) -> None:
    """Pay the reward through the owner's contract; the caller holds the view lock."""
    user = view.user
    kind = config.reward_type
    view.reward_type = kind
    view.reward_amount = config.reward_amount
    if kind == RewardedAdConfig.RewardType.CREDITS:
        from apps.wallet.api import grant_reward

        entry = grant_reward(
            user,
            EVENT_KEY,
            key=f"ads:rewarded:{view.pk}",
            reference=f"rewarded_view:{view.pk}",
        )
        view.reward_granted = entry is not None
        view.reward_amount = int(entry.amount) if entry is not None else 0
        view.reward_note = (
            f"+{view.reward_amount} credits."
            if entry is not None
            else "No credits this time: the reward rule or its daily limit blocked it."
        )
    elif kind == RewardedAdConfig.RewardType.DEVICE_RESET:
        from apps.devices.slots import grant_reset_allowance

        for index in range(config.reward_amount):
            grant_reset_allowance(
                user, key=f"ads:rewarded:{view.pk}:{index}", source="rewarded_ad"
            )
        view.reward_granted = True
        view.reward_note = (
            f"{config.reward_amount} device slot reset(s) earned. Use them on "
            "Registered browsers."
        )
    elif kind == RewardedAdConfig.RewardType.DOWNLOAD_BONUS:
        RewardedAdGrant.objects.create(
            user=user,
            view=view,
            kind=RewardedAdGrant.Kind.DOWNLOAD_BONUS,
            amount=config.reward_amount,
            expires_at=timezone.now() + timedelta(hours=config.valid_hours),
        )
        view.reward_granted = True
        view.reward_note = f"+{config.reward_amount} downloads for the next {config.valid_hours} hours."
        _forget_entitlements()
    else:
        RewardedAdGrant.objects.create(
            user=user,
            view=view,
            kind=RewardedAdGrant.Kind.AD_FREE,
            amount=config.reward_amount,
            expires_at=timezone.now() + timedelta(hours=config.reward_amount),
        )
        view.reward_granted = True
        view.reward_note = f"Ad-free for the next {config.reward_amount} hours."
        _forget_entitlements()


def _forget_entitlements() -> None:
    if django_apps.is_installed("apps.shop"):
        from apps.shop import request_cache

        request_cache.invalidate()


# --- entitlements seam ---------------------------------------------------------


def entitlement_modifier(user: Any, current: Any) -> Any:
    """Fold live rewarded-ad grants into the resolved entitlements (shop seam)."""
    from apps.shop.entitlements import grant

    live = RewardedAdGrant.objects.filter(user=user, expires_at__gt=timezone.now())
    bonus = 0
    ad_free = False
    for kind, amount in live.values_list("kind", "amount"):
        if kind == RewardedAdGrant.Kind.DOWNLOAD_BONUS:
            bonus += amount
        else:
            ad_free = True
    if bonus:
        current = grant(current, f"Rewarded ad: +{bonus} files", download_quota=bonus)
    if ad_free:
        current = replace(
            current,
            perks=replace(current.perks, ad_free=True),
            sources=(*current.sources, "Rewarded ad: ad-free"),
        )
    return current


def user_is_ad_free(user: Any) -> bool:
    """Package perk or a live rewarded ad-free period; one answer for the ads renderer."""
    if django_apps.is_installed("apps.shop"):
        from apps.shop.entitlements import effective_entitlements

        return bool(effective_entitlements(user).perks.ad_free)
    return RewardedAdGrant.objects.filter(
        user=user,
        kind=RewardedAdGrant.Kind.AD_FREE,
        expires_at__gt=timezone.now(),
    ).exists()


# --- analytics -----------------------------------------------------------------


def analytics(*, since: datetime | None = None) -> Analytics:
    """Aggregate the rewarded-ad funnel for the admin dashboard."""
    views = RewardedAdView.objects.all()
    if since is not None:
        views = views.filter(created_at__gte=since)
    counts = views.aggregate(
        starts=Count("id", filter=~Q(status=RewardedAdView.Status.REJECTED)),
        completions=Count("id", filter=Q(status=RewardedAdView.Status.COMPLETED)),
        rejected=Count("id", filter=Q(status=RewardedAdView.Status.REJECTED)),
        expired=Count("id", filter=Q(status=RewardedAdView.Status.EXPIRED)),
        flagged=Count("id", filter=Q(is_flagged=True)),
        rewards=Count("id", filter=Q(reward_granted=True)),
    )
    by_type = {
        row["reward_type"]: int(row["total"] or 0)
        for row in views.filter(reward_granted=True)
        .values("reward_type")
        .annotate(total=Sum("reward_amount"))
    }
    return Analytics(
        starts=counts["starts"],
        completions=counts["completions"],
        rejected=counts["rejected"],
        expired=counts["expired"],
        flagged=counts["flagged"],
        rewards_granted=counts["rewards"],
        by_type=by_type,
    )


def page_url(config: RewardedAdConfig) -> str:
    return reverse("ads:rewarded", kwargs={"config_id": config.pk})
