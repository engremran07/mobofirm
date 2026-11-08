from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.gamification.constants import (
    METRIC_STREAK,
    METRIC_XP_TOTAL,
)

BADGE_ICONS = [
    ("award", "Award"),
    ("trophy", "Trophy"),
    ("medal", "Medal"),
    ("star", "Star"),
    ("flame", "Flame"),
    ("gift", "Gift"),
    ("badge-check", "Verified"),
    ("download", "Download"),
    ("upload", "Upload"),
    ("message-circle", "Comment"),
    ("thumbs-up", "Thumbs up"),
    ("shield-check", "Shield"),
]
METRIC_CHOICES = [
    (METRIC_XP_TOTAL, "Total XP"),
    (METRIC_STREAK, "Daily streak (days)"),
    ("firmware_approved", "Approved firmware uploads"),
    ("requests_fulfilled", "Fulfilled firmware requests"),
    ("comments_approved", "Approved comments"),
    ("referrals_rewarded", "Verified referrals"),
    ("bounties_fulfilled", "Fulfilled bounties"),
]


class Level(models.Model):
    """XP threshold and name of a level."""

    number = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=60)
    min_xp = models.PositiveIntegerField(unique=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["number"]

    def __str__(self) -> str:
        return f"Level {self.number}: {self.name} ({self.min_xp} XP)"


class XPRule(models.Model):
    """What an event is worth. Rows are created from ``constants.XP_EVENTS``."""

    event_key = models.CharField(max_length=60, unique=True, editable=False)
    label = models.CharField(max_length=160, editable=False)
    xp = models.PositiveIntegerField(default=0)
    daily_cap = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Most awards of this event per user per day; 0 = no cap."),
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["event_key"]
        verbose_name = _("XP rule")

    def __str__(self) -> str:
        return f"{self.label} (+{self.xp} XP)"


class Badge(models.Model):
    """A badge and the single measurable condition that earns it."""

    class Tier(models.TextChoices):
        BRONZE = "bronze", "Bronze"
        SILVER = "silver", "Silver"
        GOLD = "gold", "Gold"

    slug = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=240)
    icon = models.CharField(max_length=24, choices=BADGE_ICONS, default="award")
    tier = models.CharField(max_length=8, choices=Tier.choices, default=Tier.BRONZE)
    metric = models.CharField(max_length=24, choices=METRIC_CHOICES)
    threshold = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text=_("Earned when the metric reaches this."),
    )
    xp_bonus = models.PositiveIntegerField(default=0)
    credits = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Wallet credits paid once when earned (0 = none; needs the wallet)."
        ),
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering: ClassVar[list[str]] = ["sort_order", "threshold", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def condition(self) -> str:
        label = dict(METRIC_CHOICES)[self.metric].lower()
        return f"{label}: {self.threshold}"


class UserBadge(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_badges"
    )
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name="awards")
    earned_at = models.DateTimeField(auto_now_add=True)
    awarded_manually = models.BooleanField(default=False)

    class Meta:
        ordering: ClassVar[list[str]] = ["-earned_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "badge"], name="gamification_badge_once"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.badge}"


class XPTransaction(models.Model):
    """Append-only XP ledger. ``key`` makes every grant idempotent."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="xp_transactions",
    )
    event_key = models.CharField(max_length=60, db_index=True)
    xp = models.IntegerField()
    key = models.CharField(max_length=190, unique=True)
    reference = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "event_key"], name="gamification_user_event_idx"
            ),
        ]
        verbose_name = _("XP transaction")

    def __str__(self) -> str:
        return f"{self.user} {self.xp:+d} XP ({self.event_key})"


class Streak(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="streak"
    )
    count = models.PositiveIntegerField(default=0)
    longest = models.PositiveIntegerField(default=0)
    last_active = models.DateField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.user}: {self.count} day(s)"
