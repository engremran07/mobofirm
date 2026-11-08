"""Credits economy storage: wallets, the append-only ledger, rules, prices and requests.

Credits are non-negative whole numbers (no fractions, no external currency). Every
balance change is a ``LedgerEntry``; ``Wallet.balance`` is the running total and is
only ever written by ``apps.wallet.services`` under a row lock.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar, NoReturn

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q, QuerySet
from solo.models import SingletonModel


class LedgerReason(models.TextChoices):
    """Why a ledger row exists. Only ``EARNING``/``BOUNTY`` credits can be paid out."""

    REWARD = "reward", "Reward"
    SPEND = "spend", "Spend"
    REFUND = "refund", "Refund"
    TOPUP = "topup", "Top-up"
    EARNING = "earning", "Seller earning"
    BOUNTY = "bounty", "Bounty"
    ESCROW = "escrow", "Escrow settlement"
    PAYOUT = "payout", "Payout"
    PAYOUT_REVERSAL = "payout_reversal", "Payout reversal"
    ADJUSTMENT = "adjustment", "Staff adjustment"
    PROMOTION = "promotion", "Promotion"


# Credits that were earned by contributing or selling, so they may leave the platform.
WITHDRAWABLE_REASONS: tuple[str, ...] = (
    LedgerReason.EARNING.value,
    LedgerReason.BOUNTY.value,
)


class WalletSettings(SingletonModel):
    """The single admin-editable home for credit conversion and payout limits."""

    topups_enabled = models.BooleanField(
        default=True,
        help_text="Users may request an offline top-up that staff confirm.",
    )
    payouts_enabled = models.BooleanField(
        default=True,
        help_text="Users may request a staff-reviewed payout of earned credits.",
    )
    currency_label = models.CharField(
        max_length=8,
        default="USD",
        help_text="Currency every price is quoted in (shown beside credits).",
    )
    credits_per_currency = models.PositiveIntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(1_000_000)],
        help_text=(
            "THE exchange rate: credits that equal one unit of the currency. "
            "At 10, a $4 file costs 40 credits and a $9 package costs 90. "
            "Every price in dollars is converted with this one number."
        ),
    )
    min_topup_credits = models.PositiveIntegerField(default=50)
    max_topup_credits = models.PositiveIntegerField(default=100000)
    max_open_topups = models.PositiveSmallIntegerField(
        default=3, help_text="Pending top-up requests allowed per user."
    )
    min_payout_credits = models.PositiveIntegerField(default=500)
    payout_fee_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Handling fee withheld from the cash value of a payout.",
    )

    class Meta:
        verbose_name = "Credits & pricing"
        verbose_name_plural = "Credits & pricing"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(credits_per_currency__gte=1),
                name="wallet_settings_rate_positive",
            ),
            models.CheckConstraint(
                condition=Q(payout_fee_percent__gte=0, payout_fee_percent__lte=100),
                name="wallet_settings_fee_percent_range",
            ),
        ]

    def __str__(self) -> str:
        return "Wallet settings"


class Wallet(models.Model):
    """One wallet per user. ``held`` credits are reserved by escrow and unspendable."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet"
    )
    balance = models.PositiveBigIntegerField(default=0)
    held = models.PositiveBigIntegerField(default=0)
    is_frozen = models.BooleanField(default=False)
    frozen_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Wallet"
        permissions = (("adjust_wallet", "Can adjust and freeze wallets"),)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(held__lte=F("balance")), name="wallet_held_within_balance"
            ),
        ]

    def __str__(self) -> str:
        return f"Wallet of {self.user_id}: {self.balance} credits"

    @property
    def available(self) -> int:
        return int(self.balance) - int(self.held)


class AppendOnlyQuerySet(QuerySet["LedgerEntry"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise TypeError("Ledger entries are append-only.")

    def delete(self) -> NoReturn:
        raise TypeError("Ledger entries are append-only.")

    def bulk_update(self, objs: Any, fields: Any, batch_size: Any = None) -> NoReturn:
        raise TypeError("Ledger entries are append-only.")


class LedgerEntry(models.Model):
    """One immutable balance change with the running balance after it."""

    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    wallet = models.ForeignKey(
        Wallet, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    direction = models.CharField(max_length=6, choices=Direction.choices)
    reason = models.CharField(max_length=20, choices=LedgerReason.choices)
    event_key = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_index=True,
        help_text="Reward event or spend sink that produced this row.",
    )
    amount = models.PositiveBigIntegerField()
    balance_after = models.PositiveBigIntegerField()
    key = models.CharField(
        max_length=200, unique=True, help_text="Idempotency key, globally unique."
    )
    reference = models.CharField(max_length=200, blank=True, default="", db_index=True)
    note = models.CharField(max_length=255, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    snapshot = models.JSONField(
        default=dict, blank=True, help_text="Rule, price and modifier details."
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        verbose_name = "Ledger entry"
        verbose_name_plural = "Ledger entries"
        ordering = ("-id",)
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["wallet", "-id"], name="wallet_ledger_wallet_idx")
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="wallet_ledger_amount_positive"
            ),
        ]

    def __str__(self) -> str:
        sign = "+" if self.direction == self.Direction.CREDIT else "-"
        return f"{sign}{self.amount} ({self.reason}) → {self.balance_after}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise TypeError("Ledger entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("Ledger entries are append-only.")


class RewardRule(models.Model):
    """Admin-editable earn rule for one registered reward event."""

    event_key = models.CharField(max_length=80, unique=True)
    label = models.CharField(max_length=120)
    app = models.CharField(max_length=40, db_index=True)
    enabled = models.BooleanField(default=True)
    credits = models.PositiveIntegerField(default=0)
    daily_cap = models.PositiveIntegerField(
        default=0, help_text="Most credits one user can earn per day (0 = no cap)."
    )
    monthly_cap = models.PositiveIntegerField(
        default=0, help_text="Most credits one user can earn per month (0 = no cap)."
    )
    lifetime_cap = models.PositiveIntegerField(
        default=0, help_text="Most credits one user can ever earn (0 = no cap)."
    )
    cooldown_seconds = models.PositiveIntegerField(
        default=0, help_text="Minimum seconds between two grants to one user."
    )
    min_account_age_days = models.PositiveSmallIntegerField(default=0)
    requires_verified_email = models.BooleanField(default=True)
    per_object_once = models.BooleanField(
        default=True,
        help_text="A referenced object (firmware, bounty, badge…) pays once.",
    )
    dynamic_amount = models.BooleanField(
        default=False,
        editable=False,
        help_text="The caller supplies the amount (for example an escrowed bounty).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Reward rule"
        ordering = ("app", "event_key")

    def __str__(self) -> str:
        return f"{self.event_key} → {self.credits}"


class CreditPrice(models.Model):
    """Admin-editable default price for one registered spend sink."""

    sink_key = models.CharField(max_length=80, unique=True)
    label = models.CharField(max_length=120)
    app = models.CharField(max_length=40, db_index=True)
    credits = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credit price"
        ordering = ("app", "sink_key")

    def __str__(self) -> str:
        return f"{self.sink_key}: {self.credits}"


class RewardRefusal(models.Model):
    """A reward a rule or an anti-abuse check refused (visible to staff)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    event_key = models.CharField(max_length=80, db_index=True)
    key = models.CharField(max_length=200)
    reason = models.CharField(max_length=40, db_index=True)
    detail = models.CharField(max_length=255, blank=True, default="")
    credits = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Reward refusal"
        ordering = ("-id",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["key", "reason"], name="wallet_refusal_key_reason_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_key}: {self.reason}"


class EscrowHold(models.Model):
    """Credits reserved from a wallet until they are released or cancelled."""

    class Status(models.TextChoices):
        HOLDING = "holding", "Holding"
        RELEASED = "released", "Released"
        CANCELLED = "cancelled", "Cancelled"

    wallet = models.ForeignKey(
        Wallet, on_delete=models.PROTECT, related_name="escrow_holds"
    )
    credits = models.PositiveBigIntegerField()
    key = models.CharField(max_length=150, unique=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    reference = models.CharField(max_length=200, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.HOLDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Escrow hold"
        ordering = ("-id",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(credits__gt=0), name="wallet_escrow_credits_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"Escrow {self.credits} [{self.status}]"


class TopUpRequest(models.Model):
    """An offline top-up: staff confirm the payment, then the credits are granted."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    credits = models.PositiveBigIntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency_label = models.CharField(max_length=8)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    order_reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text="Shop order that carries this top-up, when the shop exposes one.",
    )
    payment_reference = models.CharField(max_length=200, blank=True, default="")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Top-up request"
        ordering = ("-id",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(credits__gt=0), name="wallet_topup_credits_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"Top-up #{self.pk}: {self.credits} credits [{self.status}]"


class PayoutRequest(models.Model):
    """A staff-reviewed request to cash out earned credits. Credits are held at once."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"

    class Method(models.TextChoices):
        BANK = "bank", "Bank transfer"
        PAYPAL = "paypal", "PayPal"
        CRYPTO = "crypto", "Crypto"

    wallet = models.ForeignKey(
        Wallet, on_delete=models.PROTECT, related_name="payout_requests"
    )
    credits = models.PositiveBigIntegerField()
    payout_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency_label = models.CharField(max_length=8)
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    method = models.CharField(max_length=10, choices=Method.choices)
    destination = models.CharField(max_length=255)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    decision_reason = models.CharField(max_length=1000, blank=True, default="")
    payment_reference = models.CharField(max_length=200, blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Payout request"
        ordering = ("-id",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(credits__gt=0), name="wallet_payout_credits_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"Payout #{self.pk}: {self.credits} credits [{self.status}]"
