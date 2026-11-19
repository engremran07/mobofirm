"""Credits economy storage: wallets, the append-only ledger, rules, prices and requests.

Credits are non-negative whole numbers (no fractions, no external currency). Every
balance change is a ``LedgerEntry``; ``Wallet.balance`` is the running total and is
only ever written by ``apps.wallet.services`` under a row lock.

Integrity layers, from the code outwards:

* ``LedgerEntry`` refuses ``save`` on an existing row, ``delete``, and queryset
  ``update``/``delete``/``bulk_*``; ``Wallet`` refuses writes to ``balance``/``held``
  outside :func:`ledger_posting` (the window the services open while they post).
* Migration ``0003`` adds a PostgreSQL trigger that rejects any UPDATE of a money
  column and any DELETE of a ledger row, and CHECK constraints on the ledger.
* ``apps.wallet.reconcile`` recomputes every balance from the ledger and reports drift.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from itertools import pairwise
from typing import Any, ClassVar, NoReturn

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import connections, models, transaction
from django.db.models import F, Q, QuerySet
from solo.models import SingletonModel

_POSTING_WINDOW: ContextVar[bool] = ContextVar("wallet_posting_window", default=False)
BALANCE_FIELDS = frozenset({"balance", "held"})
APPEND_ONLY_MESSAGE = "Ledger entries are append-only."
# Transaction-local database setting that lets the ledger trigger accept a note change.
SCRUB_SETTING = "wallet.ledger_scrub"


@contextmanager
def ledger_posting() -> Iterator[None]:
    """The only window in which ``Wallet.balance``/``held`` may be written.

    Opened by ``apps.wallet.services`` around a posting (ledger row + counter update)
    and by the audited repair. Anything else that writes the counters raises
    ``TypeError``.
    """
    token = _POSTING_WINDOW.set(True)
    try:
        yield
    finally:
        _POSTING_WINDOW.reset(token)


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

    def clean(self) -> None:
        super().clean()
        if self.min_topup_credits > self.max_topup_credits:
            raise ValidationError(
                {"min_topup_credits": "The minimum top-up cannot exceed the maximum."}
            )


class WalletQuerySet(QuerySet["Wallet"]):
    """Refuses bulk writes to the balance counters outside :func:`ledger_posting`."""

    def update(self, **kwargs: Any) -> int:
        _refuse_counter_write(BALANCE_FIELDS & kwargs.keys())
        return super().update(**kwargs)

    def bulk_update(
        self,
        objs: Iterable[Any],
        fields: Iterable[str],
        batch_size: int | None = None,
    ) -> int:
        chosen = list(fields)
        _refuse_counter_write(BALANCE_FIELDS & set(chosen))
        return super().bulk_update(objs, chosen, batch_size)


def _refuse_counter_write(fields: Iterable[str]) -> None:
    touched = sorted(fields)
    if touched and not _POSTING_WINDOW.get():
        raise TypeError(
            f"Wallet {', '.join(touched)} change only through apps.wallet.services "
            "(every change needs a ledger entry)."
        )


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

    objects = WalletQuerySet.as_manager()

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

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Write the wallet; the counters only inside :func:`ledger_posting`.

        A full ``save()`` of an existing wallet would write a possibly stale
        ``balance``/``held`` back, so it needs ``update_fields`` without the counters.
        """
        if self._state.adding:
            _refuse_counter_write(
                name for name in BALANCE_FIELDS if getattr(self, name)
            )
        else:
            fields = kwargs.get("update_fields")
            _refuse_counter_write(
                BALANCE_FIELDS if fields is None else BALANCE_FIELDS & set(fields)
            )
        super().save(*args, **kwargs)

    @property
    def available(self) -> int:
        """Spendable credits: the balance minus what escrow holds."""
        return int(self.balance) - int(self.held)


class AppendOnlyMixin:
    """Queryset methods that would rewrite or remove rows are refused."""

    def update(self, **kwargs: Any) -> NoReturn:
        """Refused: rows are never rewritten in bulk."""
        raise TypeError(APPEND_ONLY_MESSAGE)

    def delete(self) -> NoReturn:
        """Refused: rows are never removed in bulk."""
        raise TypeError(APPEND_ONLY_MESSAGE)

    def bulk_update(self, *args: Any, **kwargs: Any) -> NoReturn:
        """Refused: rows are never rewritten in bulk."""
        raise TypeError(APPEND_ONLY_MESSAGE)

    def bulk_create(self, *args: Any, **kwargs: Any) -> NoReturn:
        """Refused: rows are written one at a time through the wallet services."""
        raise TypeError("Rows are written one at a time through the wallet services.")


class AppendOnlyQuerySet(AppendOnlyMixin, QuerySet["LedgerEntry"]):
    """Ledger queryset: only :meth:`redact` may change stored rows (account erasure)."""

    REDACTABLE: ClassVar[frozenset[str]] = frozenset({"note", "actor"})

    def redact(self, **fields: Any) -> int:
        """Blank personal data on existing rows; money columns can never be touched.

        On PostgreSQL the ledger trigger only lets ``note`` change inside a transaction
        that opened the scrub window, so the window and the update share one.
        """
        unknown = set(fields) - self.REDACTABLE
        if unknown:
            raise TypeError(f"Ledger columns {sorted(unknown)} cannot be redacted.")
        with transaction.atomic(using=self.db):
            if connections[self.db].vendor == "postgresql":
                with connections[self.db].cursor() as cursor:
                    cursor.execute("SELECT set_config(%s, 'on', true)", [SCRUB_SETTING])
            return QuerySet.update(self, **fields)


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
            models.Index(fields=["wallet", "-id"], name="wallet_ledger_wallet_idx"),
            models.Index(
                fields=["wallet", "event_key", "created_at"],
                name="wallet_ledger_event_idx",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="wallet_ledger_amount_positive"
            ),
            models.CheckConstraint(
                condition=Q(direction__in=("credit", "debit")),
                name="wallet_ledger_direction_valid",
            ),
            models.CheckConstraint(
                condition=~Q(key=""), name="wallet_ledger_key_not_empty"
            ),
        ]

    def __str__(self) -> str:
        sign = "+" if self.direction == self.Direction.CREDIT else "-"
        return f"{sign}{self.amount} ({self.reason}) → {self.balance_after}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Insert only: a second ``save``, or one reusing a primary key, is refused."""
        if not self._state.adding:
            raise TypeError(APPEND_ONLY_MESSAGE)
        kwargs["force_insert"] = True
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

    def clean(self) -> None:
        """A shorter window cannot allow more than a longer one (0 means no cap)."""
        super().clean()
        windows = (
            ("daily_cap", self.daily_cap),
            ("monthly_cap", self.monthly_cap),
            ("lifetime_cap", self.lifetime_cap),
        )
        capped = [(name, cap) for name, cap in windows if cap]
        for (short_name, short_cap), (long_name, long_cap) in pairwise(capped):
            if short_cap > long_cap:
                raise ValidationError(
                    {
                        short_name: (
                            f"The {short_name.replace('_', ' ')} cannot exceed the "
                            f"{long_name.replace('_', ' ')}."
                        )
                    }
                )


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
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["wallet", "status"], name="wallet_escrow_wstatus_idx"),
        ]
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
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "status"], name="wallet_topup_user_status_idx"
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(credits__gt=0), name="wallet_topup_credits_positive"
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0), name="wallet_topup_price_non_negative"
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
            models.CheckConstraint(
                condition=Q(payout_amount__gte=0),
                name="wallet_payout_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(fee_percent__gte=0, fee_percent__lte=100),
                name="wallet_payout_fee_percent_range",
            ),
        ]

    def __str__(self) -> str:
        return f"Payout #{self.pk}: {self.credits} credits [{self.status}]"


class WalletRepairQuerySet(AppendOnlyMixin, QuerySet["WalletRepair"]):
    """Repair records are never rewritten or removed."""


class WalletRepair(models.Model):
    """Audit record of one explicit repair of a wallet's counters from its ledger.

    The ledger is the source of truth; a repair rewrites ``balance``/``held`` to what
    the ledger and the open escrow holds say, never the other way round.
    """

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="repairs")
    previous_balance = models.PositiveBigIntegerField()
    repaired_balance = models.PositiveBigIntegerField()
    previous_held = models.PositiveBigIntegerField()
    repaired_held = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=255)
    key = models.CharField(
        max_length=200, unique=True, help_text="Idempotency key of the repair."
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = WalletRepairQuerySet.as_manager()

    class Meta:
        verbose_name = "Wallet repair"
        ordering = ("-id",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~Q(previous_balance=F("repaired_balance"))
                | ~Q(previous_held=F("repaired_held")),
                name="wallet_repair_changes_something",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"Repair of wallet {self.wallet_id}: "
            f"{self.previous_balance}→{self.repaired_balance}"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise TypeError("Repair records are append-only.")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("Repair records are append-only.")
