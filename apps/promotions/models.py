"""Promotion (campaign) models: definition, per-user grants, earn progress and audit log.

A promotion never keeps balances or quotas of its own: it plugs into the shop
entitlement resolver and the wallet reward/credit contracts (see ``effects``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .rules import EarnRuleError, EarnRules, parse_earn_rules

MAX_MULTIPLIER = Decimal("10.00")


class PromotionKind(models.TextChoices):
    FREE_TIER_GRANT = "free_tier_grant", "Free package grant"
    DISCOUNTED_PACKAGE = "discounted_package", "Discounted package"
    BONUS_QUOTA = "bonus_quota", "Bonus download quota"
    BONUS_DEVICES = "bonus_devices", "Bonus device slots"
    EARN_BY_CONTRIBUTING = "earn_by_contributing", "Earn by contributing"
    REFERRAL_BOOST = "referral_boost", "Referral boost"
    REWARD_MULTIPLIER = "reward_multiplier", "Reward multiplier"
    CREDITS_BONUS = "credits_bonus", "Credits bonus"


# Kinds a user claims with one confirmed click.
CLAIMABLE_KINDS = frozenset(
    {
        PromotionKind.FREE_TIER_GRANT,
        PromotionKind.BONUS_QUOTA,
        PromotionKind.BONUS_DEVICES,
        PromotionKind.CREDITS_BONUS,
    }
)
# Kinds whose grants add entitlements (they expire).
ENTITLEMENT_KINDS = frozenset(
    {
        PromotionKind.FREE_TIER_GRANT,
        PromotionKind.BONUS_QUOTA,
        PromotionKind.BONUS_DEVICES,
        PromotionKind.EARN_BY_CONTRIBUTING,
    }
)
# Kinds that change what wallet rewards pay while the promotion is live.
REWARD_KINDS = frozenset(
    {PromotionKind.REWARD_MULTIPLIER, PromotionKind.REFERRAL_BOOST}
)
DEFAULT_REFERRAL_EVENTS = ("referral.verified",)


class Audience(models.TextChoices):
    EVERYONE = "everyone", "Everyone"
    NEW_USERS = "new_users", "New users"
    SELLERS = "sellers", "Approved sellers"
    PACKAGE_HOLDERS = "package_holders", "Package holders"
    VERIFIED_EMAIL = "verified_email", "Verified email"


class PromotionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"


class Promotion(models.Model):
    """One campaign: what it gives, who gets it, when, and how much it may cost."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    headline = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="One line used in the site banner and notifications.",
    )
    description = models.TextField(blank=True, default="")
    terms = models.TextField(
        blank=True,
        default="",
        help_text="Shown on the offer page, one rule per line.",
    )
    kind = models.CharField(max_length=24, choices=PromotionKind.choices)
    audience = models.CharField(
        max_length=20, choices=Audience.choices, default=Audience.EVERYONE
    )
    status = models.CharField(
        max_length=10,
        choices=PromotionStatus.choices,
        default=PromotionStatus.DRAFT,
        db_index=True,
    )
    starts_at = models.DateTimeField(
        null=True, blank=True, help_text="Empty = starts as soon as it is scheduled."
    )
    ends_at = models.DateTimeField(
        null=True, blank=True, help_text="Empty = runs until staff end it."
    )

    target_package = models.ForeignKey(
        "shop.PackageTier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="promotions",
        help_text="Package granted or discounted by this offer.",
    )
    discounted_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    discounted_credit_price = models.PositiveIntegerField(null=True, blank=True)

    bonus_files = models.PositiveIntegerField(default=0)
    bonus_size_gb = models.PositiveIntegerField(default=0)
    bonus_devices = models.PositiveSmallIntegerField(default=0)
    trial_days = models.PositiveIntegerField(
        default=0,
        help_text="How long a grant lasts. 0 = until the promotion ends.",
    )

    event_keys = models.JSONField(
        default=list,
        blank=True,
        help_text="Wallet reward event keys this promotion boosts.",
    )
    reward_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal("1.00")
    )
    bonus_credits = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Credits bonus: paid once per claim. Reward boost: added to every "
            "boosted reward."
        ),
    )
    earn_rules = models.JSONField(
        default=dict,
        blank=True,
        help_text='{"events": ["contribution.approved"], "count": 3, "within_days": 30}',
    )

    max_claims_total = models.PositiveIntegerField(
        default=0, help_text="0 = unlimited."
    )
    max_claims_per_user = models.PositiveSmallIntegerField(default=1)
    budget_credits = models.PositiveIntegerField(
        default=0, help_text="Most credits this promotion may hand out. 0 = unlimited."
    )

    announce = models.BooleanField(
        default=True, help_text="Publish a banner and notifications while active."
    )
    cta_label = models.CharField(max_length=60, default="See the offer")
    announcement = models.OneToOneField(
        "users.Announcement",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="promotion",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_promotions",
    )

    page_views = models.PositiveIntegerField(default=0, editable=False)
    banner_views = models.PositiveIntegerField(default=0, editable=False)
    announcement_clicks = models.PositiveIntegerField(default=0, editable=False)
    claim_count = models.PositiveIntegerField(default=0, editable=False)
    conversion_count = models.PositiveIntegerField(default=0, editable=False)
    credits_granted = models.PositiveIntegerField(default=0, editable=False)
    notify_cursor = models.PositiveBigIntegerField(default=0, editable=False)
    notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    ended_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Promotion"
        verbose_name_plural = "Promotions"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(ends_at__isnull=True)
                | Q(starts_at__isnull=True)
                | Q(ends_at__gt=models.F("starts_at")),
                name="promotions_window_ordered",
                violation_error_message="The end must be after the start.",
            ),
            models.CheckConstraint(
                condition=Q(reward_multiplier__gte=1) & Q(reward_multiplier__lte=10),
                name="promotions_multiplier_range",
                violation_error_message="The multiplier must be between 1 and 10.",
            ),
            models.CheckConstraint(
                condition=Q(max_claims_per_user__gte=1),
                name="promotions_per_user_cap_positive",
                violation_error_message="Each user must be allowed at least one claim.",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["status", "starts_at"], name="promo_status_start_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("promotions:offer", kwargs={"slug": self.slug})

    @property
    def is_claimable(self) -> bool:
        return self.kind in CLAIMABLE_KINDS

    @property
    def is_live(self) -> bool:
        """Active and inside its window, whatever the last status sync said."""
        if self.status != PromotionStatus.ACTIVE:
            return False
        now = timezone.now()
        if self.starts_at and self.starts_at > now:
            return False
        return not (self.ends_at and self.ends_at <= now)

    @property
    def boosted_events(self) -> tuple[str, ...]:
        keys = tuple(str(key) for key in (self.event_keys or []))
        if not keys and self.kind == PromotionKind.REFERRAL_BOOST:
            return DEFAULT_REFERRAL_EVENTS
        return keys

    def rules(self) -> EarnRules | None:
        """The typed earn rules, ``None`` for kinds that have none."""
        if self.kind != PromotionKind.EARN_BY_CONTRIBUTING:
            return None
        try:
            return parse_earn_rules(self.earn_rules)
        except EarnRuleError:
            return None

    def grant_expiry(self, now: datetime) -> datetime | None:
        """When a grant made at ``now`` stops: trial days, else the promotion end."""
        if self.trial_days:
            return now + timedelta(days=self.trial_days)
        return self.ends_at

    def effect_summary(self) -> str:
        """Plain-language description of what the promotion gives."""
        parts: list[str] = []
        package = self.target_package
        if self.kind == PromotionKind.DISCOUNTED_PACKAGE and package is not None:
            prices: list[str] = []
            if self.discounted_price is not None:
                prices.append(f"{self.discounted_price} instead of {package.price}")
            if self.discounted_credit_price is not None:
                prices.append(f"{self.discounted_credit_price} credits")
            parts.append(f"{package.name} for {' or '.join(prices)}")
        elif package is not None:
            days = f" for {self.trial_days} days" if self.trial_days else ""
            parts.append(f"{package.name} package{days}")
        if self.bonus_files:
            parts.append(f"+{self.bonus_files} files")
        if self.bonus_size_gb:
            parts.append(f"+{self.bonus_size_gb} GB")
        if self.bonus_devices:
            parts.append(f"+{self.bonus_devices} device slots")
        if self.kind in REWARD_KINDS:
            if self.reward_multiplier > 1:
                multiplier = Decimal(self.reward_multiplier).normalize()
                parts.append(f"{multiplier:f}× credits")
            if self.bonus_credits:
                parts.append(f"+{self.bonus_credits} credits per reward")
        elif self.bonus_credits:
            parts.append(f"{self.bonus_credits} bonus credits")
        return ", ".join(parts) or self.get_kind_display()

    def clean(self) -> None:
        errors: dict[str, str] = {}
        self._clean_kind(errors)
        self._clean_window(errors)
        if errors:
            raise ValidationError(errors)

    def _clean_window(self, errors: dict[str, str]) -> None:
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "The end must be after the start."
        if self.kind in ENTITLEMENT_KINDS and not (self.trial_days or self.ends_at):
            errors["trial_days"] = (
                "Set the grant length in days or an end date so grants expire."
            )

    def _clean_kind(self, errors: dict[str, str]) -> None:
        kind = self.kind
        has_bonus = bool(self.bonus_files or self.bonus_size_gb or self.bonus_devices)
        if kind in (
            PromotionKind.FREE_TIER_GRANT,
            PromotionKind.DISCOUNTED_PACKAGE,
        ) and (self.target_package_id is None):
            errors["target_package"] = "Choose the package this offer is about."
        if kind == PromotionKind.DISCOUNTED_PACKAGE:
            self._clean_discount(errors)
        if kind == PromotionKind.BONUS_QUOTA and not (
            self.bonus_files or self.bonus_size_gb
        ):
            errors["bonus_files"] = "Set bonus files or bonus size."
        if kind == PromotionKind.BONUS_DEVICES and not self.bonus_devices:
            errors["bonus_devices"] = "Set the number of extra device slots."
        if kind == PromotionKind.CREDITS_BONUS and not self.bonus_credits:
            errors["bonus_credits"] = "Set how many credits are paid."
        if kind in REWARD_KINDS:
            if self.reward_multiplier <= 1 and not self.bonus_credits:
                errors["reward_multiplier"] = (
                    "Set a multiplier above 1 or bonus credits per reward."
                )
            if kind == PromotionKind.REWARD_MULTIPLIER and not self.boosted_events:
                errors["event_keys"] = "List the reward event keys to boost."
        if kind == PromotionKind.EARN_BY_CONTRIBUTING:
            try:
                parse_earn_rules(self.earn_rules)
            except EarnRuleError as error:
                errors["earn_rules"] = str(error)
            if not (
                self.target_package_id is not None or has_bonus or self.bonus_credits
            ):
                errors["target_package"] = (
                    "Choose what completing the rules earns: a package, a bonus "
                    "or credits."
                )

    def _clean_discount(self, errors: dict[str, str]) -> None:
        if self.discounted_price is None and self.discounted_credit_price is None:
            errors["discounted_price"] = "Set a discounted price or credit price."
        if self.discounted_price is not None and self.discounted_price <= 0:
            errors["discounted_price"] = "A discounted price must be above zero."
        package = self.target_package
        if package is None:
            return
        if self.discounted_price is not None and self.discounted_price >= package.price:
            errors["discounted_price"] = (
                f"The discount must be below the regular price ({package.price})."
            )
        if self.discounted_credit_price is not None and not package.credit_price:
            errors["discounted_credit_price"] = (
                "This package cannot be bought with credits."
            )
        elif (
            self.discounted_credit_price is not None
            and self.discounted_credit_price >= package.credit_price
        ):
            errors["discounted_credit_price"] = (
                "The credit discount must be below the regular credit price "
                f"({package.credit_price})."
            )


class GrantOutcome(models.TextChoices):
    GRANTED = "granted", "Granted"
    REVOKED = "revoked", "Revoked"


class PromotionGrant(models.Model):
    """What one user received from one promotion; idempotent per trigger."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="promotion_grants",
    )
    promotion = models.ForeignKey(
        Promotion, on_delete=models.CASCADE, related_name="grants"
    )
    trigger = models.CharField(
        max_length=120,
        help_text="claim, earn:<cycle> or order:<id>; unique per user and promotion.",
    )
    outcome = models.CharField(
        max_length=10, choices=GrantOutcome.choices, default=GrantOutcome.GRANTED
    )
    credits = models.PositiveIntegerField(default=0)
    detail = models.CharField(max_length=255, blank=True, default="")
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-granted_at"]
        verbose_name = "Promotion grant"
        verbose_name_plural = "Promotion grants"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "promotion", "trigger"],
                name="promotions_grant_once_per_trigger",
                violation_error_message="This promotion was already granted for this trigger.",
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "outcome", "expires_at"], name="promo_grant_user_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.promotion} → {self.user} [{self.trigger}]"

    @property
    def is_live(self) -> bool:
        if self.outcome != GrantOutcome.GRANTED:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class PromotionProgress(models.Model):
    """One counted event towards an earn rule; unique per (promotion, user, ref)."""

    promotion = models.ForeignKey(
        Promotion, on_delete=models.CASCADE, related_name="progress"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="promotion_progress",
    )
    event_key = models.CharField(max_length=80)
    ref = models.CharField(
        max_length=200, help_text="The approved object or ledger key that counted."
    )
    counted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["counted_at"]
        verbose_name = "Promotion progress"
        verbose_name_plural = "Promotion progress"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["promotion", "user", "ref"],
                name="promotions_progress_once_per_ref",
            )
        ]

    def __str__(self) -> str:
        return f"{self.promotion} · {self.user} · {self.ref}"


class PromotionLog(models.Model):
    """Append-only audit of lifecycle changes and grant decisions."""

    promotion = models.ForeignKey(
        Promotion, on_delete=models.CASCADE, related_name="log"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    action = models.CharField(max_length=40)
    detail = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        verbose_name = "Promotion log entry"
        verbose_name_plural = "Promotion log"

    def __str__(self) -> str:
        return f"{self.promotion} · {self.action}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise ValidationError("The promotion log is append-only.")
        super().save(*args, **kwargs)
