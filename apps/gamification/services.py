"""XP, levels, badges, streaks and the leaderboard.

Every grant is idempotent on a caller-supplied ``key`` (the XP ledger has a unique
key), so replayed events, retries and duplicate deliveries never double-award.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.gamification.constants import (
    BADGE_EARNED_REWARD_KEY,
    DEFAULT_BADGES,
    DEFAULT_LEVELS,
    FIRMWARE_APPROVED,
    METRIC_EVENT_COUNTS,
    METRIC_STREAK,
    METRIC_XP_TOTAL,
    XP_EVENTS,
)
from apps.gamification.models import (
    Badge,
    Level,
    Streak,
    UserBadge,
    XPRule,
    XPTransaction,
)

logger = logging.getLogger(__name__)

LEADERBOARD_PERIODS = {"week": 7, "month": 30, "all": None}
LEADERBOARD_CACHE_SECONDS = 60
BADGE_BONUS_EVENT = "badge.earned"
_MAX_BADGE_PASSES = 5


# -------------------------------------------------------------- defaults ----


def sync_defaults() -> None:
    """Create XP rules, levels and starter badges when missing (never overwrites)."""
    for event in XP_EVENTS.values():
        XPRule.objects.get_or_create(
            event_key=event.key,
            defaults={
                "label": event.label,
                "xp": event.default_xp,
                "daily_cap": event.default_daily_cap,
            },
        )
    if not Level.objects.exists():
        Level.objects.bulk_create(
            Level(number=number, name=name, min_xp=xp)
            for number, name, xp in DEFAULT_LEVELS
        )
    for order, row in enumerate(DEFAULT_BADGES, start=1):
        slug, name, description, icon, tier, metric, threshold, bonus = row
        Badge.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "description": description,
                "icon": icon,
                "tier": tier,
                "metric": metric,
                "threshold": threshold,
                "xp_bonus": bonus,
                "sort_order": order * 10,
            },
        )


# ------------------------------------------------------------------ XP ----


def total_xp(user_id: int) -> int:
    return int(
        XPTransaction.objects.filter(user_id=user_id).aggregate(total=Sum("xp"))[
            "total"
        ]
        or 0
    )


def level_for(xp: int) -> Level | None:
    return Level.objects.filter(min_xp__lte=xp).order_by("-min_xp").first()


@dataclass(frozen=True, slots=True)
class Progress:
    xp: int
    level: Level | None
    next_level: Level | None
    into_level: int
    span: int


def progress_for(user_id: int) -> Progress:
    xp = total_xp(user_id)
    level = level_for(xp)
    next_level = Level.objects.filter(min_xp__gt=xp).order_by("min_xp").first()
    floor = level.min_xp if level else 0
    span = (next_level.min_xp - floor) if next_level else 0
    return Progress(xp, level, next_level, xp - floor, span)


def _rule_for(event_key: str) -> XPRule | None:
    rule = XPRule.objects.filter(event_key=event_key).first()
    if rule is None and event_key in XP_EVENTS:
        sync_defaults()
        rule = XPRule.objects.filter(event_key=event_key).first()
    return rule


def award(
    user: Any, event_key: str, *, key: str, reference: str = ""
) -> XPTransaction | None:
    """Give ``user`` the XP configured for ``event_key``.

    Returns ``None`` (no error) when the rule is missing or disabled, the account is
    inactive, the daily cap is reached or ``key`` was already awarded.
    """
    if not getattr(user, "is_active", False):
        return None
    rule = _rule_for(event_key)
    if rule is None or not rule.is_enabled or rule.xp <= 0:
        return None
    with transaction.atomic():
        if XPTransaction.objects.filter(key=key).exists():
            return None
        if rule.daily_cap:
            start = timezone.localtime().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            today = XPTransaction.objects.filter(
                user=user, event_key=event_key, created_at__gte=start
            ).count()
            if today >= rule.daily_cap:
                return None
        try:
            with transaction.atomic():
                entry = XPTransaction.objects.create(
                    user=user,
                    event_key=event_key,
                    xp=rule.xp,
                    key=key[:190],
                    reference=reference[:200],
                )
        except IntegrityError:
            return None
        _touch_streak(user.pk)
        evaluate_badges(user)
        _mirror_reputation(user.pk)
    _drop_leaderboard_cache()
    return entry


def _mirror_reputation(user_id: int) -> None:
    """Keep ``users.Reputation`` equal to the XP total (single reputation score)."""
    reputation = apps.get_model("users", "Reputation")
    approved = XPTransaction.objects.filter(
        user_id=user_id, event_key=FIRMWARE_APPROVED
    ).count()
    reputation.objects.update_or_create(
        user_id=user_id,
        defaults={"total_score": total_xp(user_id), "fw_contributions": approved},
    )


# -------------------------------------------------------------- streaks ----


def _touch_streak(user_id: int) -> Streak:
    today = timezone.localdate()
    streak, _created = Streak.objects.select_for_update().get_or_create(user_id=user_id)
    if streak.last_active == today:
        return streak
    if streak.last_active == today - timedelta(days=1):
        streak.count += 1
    else:
        streak.count = 1
    streak.last_active = today
    streak.longest = max(streak.longest, streak.count)
    streak.save(update_fields=["count", "longest", "last_active"])
    return streak


# --------------------------------------------------------------- badges ----


def metrics_for(user_id: int) -> dict[str, int]:
    counts = dict(
        XPTransaction.objects.filter(
            user_id=user_id, event_key__in=METRIC_EVENT_COUNTS.values()
        )
        .values_list("event_key")
        .annotate(total=Count("id"))
    )
    metrics = {
        metric: int(counts.get(event_key, 0))
        for metric, event_key in METRIC_EVENT_COUNTS.items()
    }
    metrics[METRIC_XP_TOTAL] = total_xp(user_id)
    streak = Streak.objects.filter(user_id=user_id).first()
    metrics[METRIC_STREAK] = streak.count if streak else 0
    return metrics


def evaluate_badges(user: Any) -> list[Badge]:
    """Award every active badge whose condition is met; returns the new ones."""
    earned: list[Badge] = []
    for _pass in range(_MAX_BADGE_PASSES):
        held = set(
            UserBadge.objects.filter(user=user).values_list("badge_id", flat=True)
        )
        metrics = metrics_for(user.pk)
        fresh = [
            badge
            for badge in Badge.objects.filter(is_active=True).exclude(pk__in=held)
            if metrics.get(badge.metric, 0) >= badge.threshold
        ]
        if not fresh:
            break
        for badge in fresh:
            if _grant_badge(user, badge):
                earned.append(badge)
    return earned


def _grant_badge(user: Any, badge: Badge, *, manual: bool = False) -> bool:
    try:
        with transaction.atomic():
            UserBadge.objects.create(user=user, badge=badge, awarded_manually=manual)
    except IntegrityError:
        return False
    if badge.xp_bonus:
        XPTransaction.objects.get_or_create(
            key=f"badge:{user.pk}:{badge.pk}",
            defaults={
                "user": user,
                "event_key": BADGE_BONUS_EVENT,
                "xp": badge.xp_bonus,
                "reference": badge.slug,
            },
        )
    if badge.credits:
        user_id, badge_id = user.pk, badge.pk
        transaction.on_commit(lambda: _pay_badge_credits(user_id, badge_id))
    _notify_badge(user, badge)
    return True


def grant_badge(user: Any, badge: Badge) -> bool:
    """Staff action: give a badge regardless of its condition. Idempotent."""
    with transaction.atomic():
        granted = _grant_badge(user, badge, manual=True)
        if granted:
            _mirror_reputation(user.pk)
    return granted


def _pay_badge_credits(user_id: int, badge_id: int) -> None:
    """Pay the badge's credits once through the wallet contract (if installed)."""
    if not apps.is_installed("apps.wallet"):
        return
    from apps.wallet.api import WalletError, grant_reward

    badge = Badge.objects.filter(pk=badge_id).first()
    user = get_user_model().objects.filter(pk=user_id).first()
    if badge is None or user is None:
        return
    try:
        grant_reward(
            user,
            BADGE_EARNED_REWARD_KEY,
            key=f"gamification.badge:{user_id}:{badge_id}",
            reference=badge.slug,
            amount_override=badge.credits,
        )
    except WalletError:
        logger.exception("Badge credits for user %s could not be paid", user_id)


def _notify_badge(user: Any, badge: Badge) -> None:
    from apps.core.services.notifications import notification_service

    notification_service.send(
        user.pk,
        f"Badge earned: {badge.name}",
        badge.description,
        "info",
    )


# ---------------------------------------------------------- leaderboard ----


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    rank: int
    user_id: int
    username: str
    xp: int
    level: str


def _cache_key(period: str) -> str:
    return f"gamification:leaderboard:{period}"


def _drop_leaderboard_cache() -> None:
    cache.delete_many([_cache_key(period) for period in LEADERBOARD_PERIODS])


def leaderboard(period: str = "all", limit: int = 50) -> list[LeaderboardRow]:
    """Top contributors. Private-profile and inactive accounts are hidden."""
    if period not in LEADERBOARD_PERIODS:
        period = "all"
    cached = cache.get(_cache_key(period))
    if isinstance(cached, list):
        return cached[:limit]
    days = LEADERBOARD_PERIODS[period]
    entries = XPTransaction.objects.filter(user__is_active=True).exclude(
        user__profile__is_public=False
    )
    if days is not None:
        entries = entries.filter(created_at__gte=timezone.now() - timedelta(days=days))
    top = list(
        entries.values("user_id", "user__username")
        .annotate(xp=Sum("xp"))
        .filter(xp__gt=0)
        .order_by("-xp", "user_id")[:100]
    )
    totals = dict(
        XPTransaction.objects.filter(user_id__in=[row["user_id"] for row in top])
        .values_list("user_id")
        .annotate(total=Sum("xp"))
    )
    levels = list(Level.objects.order_by("-min_xp"))

    def level_name(xp: int) -> str:
        return next((lv.name for lv in levels if lv.min_xp <= xp), "")

    rows = [
        LeaderboardRow(
            rank=index,
            user_id=row["user_id"],
            username=row["user__username"] or "",
            xp=int(row["xp"]),
            level=level_name(int(totals.get(row["user_id"], 0))),
        )
        for index, row in enumerate(top, start=1)
    ]
    cache.set(_cache_key(period), rows, LEADERBOARD_CACHE_SECONDS)
    return rows[:limit]
