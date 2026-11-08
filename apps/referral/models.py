"""Referral storage: one personal code per user and one link per invited user.

The reward amount is not stored here: it is the admin-editable wallet ``RewardRule``
for ``referral.verified`` (and ``referral.welcome`` for the invitee).
"""

from __future__ import annotations

import secrets
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from solo.models import SingletonModel

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


class ReferralSettings(SingletonModel):
    """Anti-abuse and eligibility rules for referral rewards."""

    enabled = models.BooleanField(
        default=True, help_text="Capture referral codes and pay rewards."
    )
    require_qualifying_action = models.BooleanField(
        default=True,
        help_text="Reward only after the invitee's first approved contribution.",
    )
    max_rewarded_per_referrer = models.PositiveIntegerField(
        default=50, help_text="Rewarded invitees per referrer (0 = unlimited)."
    )
    max_rewarded_per_referrer_per_day = models.PositiveIntegerField(
        default=5, help_text="Rewarded invitees per referrer per day (0 = unlimited)."
    )
    block_same_network = models.BooleanField(
        default=True,
        help_text="Refuse the reward when invitee and referrer signed up from the same network.",
    )
    capture_days = models.PositiveSmallIntegerField(
        default=30, help_text="Days a captured code stays valid before sign-up."
    )

    class Meta:
        verbose_name = "Referral settings"

    def __str__(self) -> str:
        return "Referral settings"


class ReferralCode(models.Model):
    """A user's personal referral code (one per user)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_code"
    )
    code = models.CharField(max_length=20, unique=True, default=generate_code)
    is_active = models.BooleanField(default=True)
    clicks = models.PositiveIntegerField(default=0)
    network_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Hashed network of the owner's last visit (same-network checks).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Referral code"

    def __str__(self) -> str:
        return self.code


class Referral(models.Model):
    """An invited user. Created at sign-up, rewarded only once they qualify."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for verification"
        REWARDED = "rewarded", "Rewarded"
        REJECTED = "rejected", "Not rewarded"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals"
    )
    invitee = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referred_by"
    )
    code = models.CharField(max_length=20)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reject_reason = models.CharField(max_length=40, blank=True, default="")
    signup_network_hash = models.CharField(max_length=64, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)
    qualified_at = models.DateTimeField(null=True, blank=True)
    qualifying_kind = models.CharField(max_length=40, blank=True, default="")
    reward_credits = models.PositiveBigIntegerField(default=0)
    rewarded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Referral"
        ordering = ("-id",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~Q(referrer=F("invitee")), name="referral_not_self"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.referrer_id} → {self.invitee_id} [{self.status}]"
