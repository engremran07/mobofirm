"""The credits economy engine: ledger, rewards, spending, escrow, top-ups, payouts.

Rules that hold for every function here:

* Credits are whole numbers; amounts that are not positive ``int`` are rejected.
* Each balance change runs in ``transaction.atomic()`` with the wallet row locked via
  ``select_for_update(of=("self",))`` and is idempotent on its globally unique ``key``:
  repeating a call returns the original ``LedgerEntry`` and changes nothing.
* The ledger is append-only; ``Wallet.balance`` always equals credits minus debits
  (see :func:`audit_wallet`).
* Business refusals of a reward never raise: ``grant_reward`` returns ``None`` and
  records a ``RewardRefusal``. Spending beyond the balance raises ``InsufficientCredits``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from apps.core.events.bus import DomainError, EventTypes, event_bus
from apps.core.services.notifications import notification_service

from . import registry
from .models import (
    WITHDRAWABLE_REASONS,
    CreditPrice,
    EscrowHold,
    LedgerEntry,
    LedgerReason,
    PayoutRequest,
    RewardRefusal,
    RewardRule,
    TopUpRequest,
    Wallet,
    WalletSettings,
)

logger = logging.getLogger(__name__)

SHOP_ORDER_PAID_EVENT = "shop.order_paid"
WALLET_TOPUP_CONFIRMED_EVENT = "wallet.topup_confirmed"
WALLET_PAYOUT_REQUESTED_EVENT = "wallet.payout_requested"
WALLET_PAYOUT_RESOLVED_EVENT = "wallet.payout_resolved"
WALLET_REWARD_REFUSED_EVENT = "wallet.reward_refused"

CREDIT = LedgerEntry.Direction.CREDIT
DEBIT = LedgerEntry.Direction.DEBIT


class WalletError(DomainError):
    """Base class for every business error raised by the wallet."""


class InvalidAmount(WalletError, ValueError):
    """The amount is not a positive whole number of credits."""


class InsufficientCredits(WalletError):
    def __init__(self, needed: int, available: int) -> None:
        super().__init__(f"{needed} credits needed but only {available} are available.")
        self.needed = needed
        self.available = available


class WalletFrozen(WalletError):
    """Staff froze this wallet; no user-driven balance change is allowed."""


class IdempotencyConflict(WalletError):
    """The key was already used for a different operation."""


class UnknownSink(WalletError):
    """No spend sink with this key is registered."""


class SinkDisabled(WalletError):
    """The spend sink exists but staff switched it off."""


class EscrowError(WalletError):
    """An escrow hold cannot make the requested transition."""


class PayoutError(WalletError, ValueError):
    """A payout request was rejected."""


class TopUpError(WalletError, ValueError):
    """A top-up request was rejected."""


# --------------------------------------------------------------------------- helpers


def _credits(value: object, label: str = "credits") -> int:
    """Validate a positive whole number of credits (bool and float are refused)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAmount(f"{label} must be a whole number.")
    if value <= 0:
        raise InvalidAmount(f"{label} must be greater than zero.")
    return value


def _require_user(user: AbstractBaseUser | None) -> AbstractBaseUser:
    if user is None or user.pk is None:
        raise WalletError("A saved user is required.")
    return user


def get_settings() -> WalletSettings:
    return WalletSettings.get_solo()


def credits_per_currency() -> int:
    return int(get_settings().credits_per_currency)


def to_credits_at(amount: Decimal, rate: int) -> int:
    """``amount × rate`` as whole credits (half up, at least 1 for a positive amount).

    The arithmetic behind :func:`to_credits`; separate so the admin can preview a rate
    before it is saved.
    """
    try:
        value = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidAmount("The amount is not a number.") from error
    if not value.is_finite() or value < 0:
        raise InvalidAmount("The amount must be zero or more.")
    whole = int((value * rate).to_integral_value(ROUND_HALF_UP))
    return max(whole, 1) if value > 0 else 0


def to_credits(amount: Decimal) -> int:
    """The ONE conversion from a currency amount to credits: ``amount × rate``.

    The rate is ``WalletSettings.credits_per_currency``. Rounds half up, but a positive
    amount is never worth less than one credit (a priced item must not become free).
    ``0`` converts to ``0``; negative, non-finite and non-numeric amounts are refused.
    """
    return to_credits_at(amount, credits_per_currency())


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def format_currency(amount: Decimal) -> str:
    """``$4.00``: the amount in the configured display currency."""
    label = get_settings().currency_label
    symbol = CURRENCY_SYMBOLS.get(label, f"{label} ")
    return f"{symbol}{Decimal(amount):.2f}"


def price_label(amount: Decimal) -> str:
    """``$4.00 · 40 credits``: every price is shown in both currencies."""
    whole = to_credits(amount)
    noun = "credit" if whole == 1 else "credits"
    return f"{format_currency(amount)} · {whole} {noun}"


def to_currency(credit_amount: int) -> Decimal:
    """Cash value of ``credit_amount`` credits in the display currency (two decimals)."""
    return (Decimal(credit_amount) / Decimal(credits_per_currency())).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )


def get_wallet(user: AbstractBaseUser) -> Wallet:
    """The user's wallet, created on first use."""
    _require_user(user)
    wallet, _ = Wallet.objects.get_or_create(user_id=user.pk)
    return wallet


def _lock(*wallet_ids: int) -> dict[int, Wallet]:
    """Lock wallet rows in primary-key order so concurrent transfers cannot deadlock."""
    rows = (
        Wallet.objects.select_for_update(of=("self",))
        .filter(pk__in=sorted(set(wallet_ids)))
        .order_by("pk")
    )
    return {wallet.pk: wallet for wallet in rows}


def _locked(user: AbstractBaseUser) -> Wallet:
    wallet = get_wallet(user)
    return _lock(wallet.pk)[wallet.pk]


def balance(user: AbstractBaseUser) -> int:
    """Spendable credits (total minus escrow holds); 0 for a user without a wallet."""
    _require_user(user)
    wallet = Wallet.objects.filter(user_id=user.pk).first()
    return wallet.available if wallet else 0


@dataclass(frozen=True)
class BalanceSnapshot:
    total: int
    held: int
    available: int
    withdrawable: int
    is_frozen: bool


def balance_snapshot(user: AbstractBaseUser) -> BalanceSnapshot:
    wallet = get_wallet(user)
    return BalanceSnapshot(
        total=int(wallet.balance),
        held=int(wallet.held),
        available=wallet.available,
        withdrawable=_withdrawable(wallet),
        is_frozen=wallet.is_frozen,
    )


def _publish(event: str, payload: dict[str, Any]) -> None:
    transaction.on_commit(lambda: event_bus.publish(event, payload))


def _notify(
    user_id: int, title: str, message: str, url_name: str = "wallet:dashboard"
) -> None:
    url = reverse(url_name)
    transaction.on_commit(
        lambda: notification_service.send(
            user_id, title, message, "info", action_type="award", url=url
        )
    )


def _assert_same(
    existing: LedgerEntry, wallet: Wallet, direction: str, amount: int | None
) -> None:
    if (
        existing.wallet_id != wallet.pk
        or existing.direction != direction
        or (amount is not None and existing.amount != amount)
    ):
        raise IdempotencyConflict(
            f"Key {existing.key!r} was already used for a different ledger entry."
        )


def _post(
    wallet: Wallet,
    *,
    direction: str,
    amount: int,
    reason: str,
    key: str,
    event_key: str = "",
    reference: str = "",
    note: str = "",
    actor: AbstractBaseUser | None = None,
    snapshot: Mapping[str, Any] | None = None,
    enforce_freeze: bool = True,
    from_held: int = 0,
) -> tuple[LedgerEntry, bool]:
    """Append one ledger row. ``wallet`` must be locked and fresh inside a transaction."""
    if not key or len(key) > 200:
        raise InvalidAmount("A ledger key of 1-200 characters is required.")
    existing = LedgerEntry.objects.filter(key=key).first()
    if existing is not None:
        _assert_same(existing, wallet, direction, amount)
        return existing, False
    if enforce_freeze and wallet.is_frozen:
        raise WalletFrozen("This wallet is frozen.")
    if direction == DEBIT:
        if from_held:
            if wallet.held < from_held or wallet.balance < amount:
                raise EscrowError("The escrow is no longer covered by the balance.")
            wallet.held -= from_held
        elif wallet.available < amount:
            raise InsufficientCredits(amount, wallet.available)
        wallet.balance -= amount
    else:
        wallet.balance += amount
    wallet.save(update_fields=["balance", "held", "updated_at"])
    try:
        with transaction.atomic():
            entry = LedgerEntry.objects.create(
                wallet=wallet,
                direction=direction,
                reason=reason,
                event_key=event_key,
                amount=amount,
                balance_after=wallet.balance,
                key=key,
                reference=reference[:200],
                note=note[:255],
                actor_id=actor.pk if actor is not None else None,
                snapshot=dict(snapshot or {}),
            )
    except IntegrityError as error:
        raise IdempotencyConflict(f"Key {key!r} is already in use.") from error
    _publish(
        EventTypes.WALLET_CREDITED
        if direction == CREDIT
        else EventTypes.WALLET_DEBITED,
        {
            "user_id": wallet.user_id,
            "amount": amount,
            "reason": reason,
            "event_key": event_key,
            "key": key,
            "balance_after": int(wallet.balance),
            "reference": reference,
        },
    )
    return entry, True


# ------------------------------------------------------------------ credit and debit


def credit(
    user: AbstractBaseUser,
    credit_amount: int,
    *,
    reason: LedgerReason | str,
    key: str,
    reference: str = "",
    note: str = "",
    actor: AbstractBaseUser | None = None,
) -> LedgerEntry:
    """Add credits. Idempotent on ``key``; refused while the wallet is frozen."""
    amount = _credits(credit_amount)
    with transaction.atomic():
        wallet = _locked(user)
        entry, _ = _post(
            wallet,
            direction=CREDIT,
            amount=amount,
            reason=str(reason),
            key=key,
            reference=reference,
            note=note,
            actor=actor,
        )
    return entry


def debit(
    user: AbstractBaseUser,
    credit_amount: int,
    *,
    reason: LedgerReason | str,
    key: str,
    reference: str = "",
    note: str = "",
    actor: AbstractBaseUser | None = None,
) -> LedgerEntry:
    """Remove credits. Raises ``InsufficientCredits``; idempotent on ``key``."""
    amount = _credits(credit_amount)
    with transaction.atomic():
        wallet = _locked(user)
        entry, _ = _post(
            wallet,
            direction=DEBIT,
            amount=amount,
            reason=str(reason),
            key=key,
            reference=reference,
            note=note,
            actor=actor,
        )
    return entry


def _require_perm(actor: AbstractBaseUser | None, perm: str) -> AbstractBaseUser:
    if (
        not isinstance(actor, AbstractBaseUser)
        or not isinstance(actor, PermissionsMixin)
        or not actor.is_active
        or not actor.has_perm(perm)
    ):
        raise PermissionDenied(f"Missing permission {perm}.")
    return actor


def adjust(
    user: AbstractBaseUser,
    credit_amount: int,
    *,
    actor: AbstractBaseUser,
    reason_text: str,
    key: str,
) -> LedgerEntry:
    """Staff correction: positive adds, negative removes. A written reason is required."""
    _require_perm(actor, "wallet.adjust_wallet")
    note = reason_text.strip()
    if not note:
        raise WalletError("A reason is required for an adjustment.")
    if (
        isinstance(credit_amount, bool)
        or not isinstance(credit_amount, int)
        or credit_amount == 0
    ):
        raise InvalidAmount("An adjustment needs a non-zero whole number of credits.")
    with transaction.atomic():
        wallet = _locked(user)
        entry, created = _post(
            wallet,
            direction=CREDIT if credit_amount > 0 else DEBIT,
            amount=abs(credit_amount),
            reason=LedgerReason.ADJUSTMENT,
            key=key,
            note=note,
            actor=actor,
            enforce_freeze=False,
        )
    if created:
        _notify(
            wallet.user_id,
            "Your credits were adjusted",
            f"Staff changed your balance by {credit_amount:+d} credits: {note}",
        )
    return entry


def set_frozen(
    user: AbstractBaseUser, frozen: bool, *, actor: AbstractBaseUser, reason_text: str
) -> Wallet:
    _require_perm(actor, "wallet.adjust_wallet")
    note = reason_text.strip()
    if frozen and not note:
        raise WalletError("A reason is required to freeze a wallet.")
    with transaction.atomic():
        wallet = _locked(user)
        wallet.is_frozen = frozen
        wallet.frozen_reason = note[:255] if frozen else ""
        wallet.save(update_fields=["is_frozen", "frozen_reason", "updated_at"])
    return wallet


# --------------------------------------------------------------------------- rewards


def sync_registry() -> None:
    """Create missing rule/price rows from the registered defaults (never overwrites)."""
    for event in registry.reward_events().values():
        RewardRule.objects.get_or_create(
            event_key=event.key,
            defaults={
                "label": event.label,
                "app": event.app,
                "enabled": event.default_enabled,
                "credits": event.default_credits,
                "daily_cap": event.default_daily_cap,
                "monthly_cap": event.default_monthly_cap,
                "requires_verified_email": event.requires_verified_email,
                "per_object_once": event.per_object_once,
                "dynamic_amount": event.dynamic_amount,
            },
        )
    for sink in registry.spend_sinks().values():
        CreditPrice.objects.get_or_create(
            sink_key=sink.key,
            defaults={
                "label": sink.label,
                "app": sink.app,
                "credits": sink.default_credits,
            },
        )


def _rule_for(event_key: str) -> RewardRule | None:
    rule = RewardRule.objects.filter(event_key=event_key).first()
    if rule is not None:
        return rule
    event = registry.reward_events().get(event_key)
    if event is None:
        return None
    sync_registry()
    return RewardRule.objects.filter(event_key=event_key).first()


def _is_verified(user: AbstractBaseUser) -> bool:
    return bool(getattr(user, "email_verified_at", None))


def _earned_since(wallet: Wallet, event_key: str, since: Any = None) -> int:
    entries = LedgerEntry.objects.filter(
        wallet=wallet, direction=CREDIT, reason=LedgerReason.REWARD, event_key=event_key
    )
    if since is not None:
        entries = entries.filter(created_at__gte=since)
    return int(entries.aggregate(total=Sum("amount"))["total"] or 0)


def _refuse(
    user: AbstractBaseUser,
    event_key: str,
    key: str,
    reason: str,
    detail: str,
    refused_credits: int,
) -> None:
    RewardRefusal.objects.get_or_create(
        key=key,
        reason=reason,
        defaults={
            "user_id": user.pk,
            "event_key": event_key,
            "detail": detail[:255],
            "credits": refused_credits,
        },
    )
    logger.info("Reward %s for user %s refused: %s", event_key, user.pk, reason)


def _blocked_reason(
    rule: RewardRule,
    wallet: Wallet,
    user: AbstractBaseUser,
    amount: int,
    reference: str,
    context: Mapping[str, Any],
) -> tuple[str, str] | None:
    """The first rule or anti-abuse check that refuses this grant, if any."""
    if not rule.enabled:
        return "rule_disabled", "Staff disabled this reward."
    if wallet.is_frozen:
        return "wallet_frozen", wallet.frozen_reason
    if not user.is_active:
        return "inactive_user", ""
    if rule.requires_verified_email and not _is_verified(user):
        return "email_unverified", "The account email is not verified."
    joined = getattr(user, "date_joined", None)
    if (
        rule.min_account_age_days
        and joined is not None
        and timezone.now() - joined < timedelta(days=rule.min_account_age_days)
    ):
        return "account_too_new", f"Needs {rule.min_account_age_days} days."
    source = context.get("source_user_id")
    if source is not None and str(source) == str(user.pk):
        return "self_reward", "Users cannot reward themselves."
    if (
        rule.per_object_once
        and reference
        and LedgerEntry.objects.filter(
            event_key=rule.event_key, reference=reference, reason=LedgerReason.REWARD
        ).exists()
    ):
        return "duplicate_object", "This object already paid out."
    now = timezone.now()
    if rule.cooldown_seconds:
        last = (
            LedgerEntry.objects.filter(
                wallet=wallet, event_key=rule.event_key, reason=LedgerReason.REWARD
            )
            .order_by("-id")
            .first()
        )
        if last and now - last.created_at < timedelta(seconds=rule.cooldown_seconds):
            return "cooldown", f"Wait {rule.cooldown_seconds} seconds between rewards."
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    windows = (
        ("daily_cap", rule.daily_cap, start_of_day),
        ("monthly_cap", rule.monthly_cap, start_of_day.replace(day=1)),
        ("lifetime_cap", rule.lifetime_cap, None),
    )
    for name, cap, since in windows:
        if cap and _earned_since(wallet, rule.event_key, since) + amount > cap:
            return name, f"Would exceed the {name.replace('_', ' ')} of {cap}."
    return None


def grant_reward(
    user: AbstractBaseUser,
    event_key: str,
    *,
    key: str,
    reference: str = "",
    amount_override: int | None = None,
    context: Mapping[str, Any] | None = None,
) -> LedgerEntry | None:
    """Pay a user for a registered event according to its admin-editable rule.

    Idempotent on ``key``. Returns ``None`` (and records a ``RewardRefusal``) when the
    rule, a cap or an anti-abuse check blocks it; never raises for those refusals.
    ``amount_override`` replaces the rule amount (bounties, per-badge credits); caps apply.
    """
    _require_user(user)
    ctx: Mapping[str, Any] = context or {}
    rule = _rule_for(event_key)
    if rule is None:
        raise WalletError(f"Reward event {event_key!r} is not registered.")
    with transaction.atomic():
        wallet = _locked(user)
        existing = LedgerEntry.objects.filter(key=key).first()
        if existing is not None:
            _assert_same(existing, wallet, CREDIT, None)
            return existing
        base = amount_override if amount_override is not None else rule.credits
        if isinstance(base, bool) or not isinstance(base, int) or base < 0:
            raise InvalidAmount("A reward must be a whole number of credits.")
        amount = base
        applied: list[str] = []
        for modifier in registry.reward_modifiers():
            adjusted = max(0, int(modifier(user, event_key, amount, ctx)))
            if adjusted != amount:
                applied.append(f"{modifier.__qualname__}:{amount}->{adjusted}")
                amount = adjusted
        if amount <= 0:
            _refuse(user, event_key, key, "zero_credits", "The reward is worth 0.", 0)
            return None
        blocked = _blocked_reason(rule, wallet, user, amount, reference, ctx)
        if blocked is not None:
            _refuse(user, event_key, key, blocked[0], blocked[1], amount)
            _publish(
                WALLET_REWARD_REFUSED_EVENT,
                {"user_id": user.pk, "event_key": event_key, "reason": blocked[0]},
            )
            return None
        entry, created = _post(
            wallet,
            direction=CREDIT,
            amount=amount,
            reason=LedgerReason.REWARD,
            key=key,
            event_key=event_key,
            reference=reference,
            note=rule.label,
            snapshot={
                "rule_credits": rule.credits,
                "base": base,
                "modifiers": applied,
                "daily_cap": rule.daily_cap,
                "monthly_cap": rule.monthly_cap,
                "lifetime_cap": rule.lifetime_cap,
            },
        )
    if created:
        _notify(
            wallet.user_id,
            f"You earned {amount} credits",
            f"{rule.label}: +{amount} credits.",
        )
    return entry


# ---------------------------------------------------------------------------- spending


def price_of(
    sink_key: str,
    *,
    quantity: int = 1,
    context: Mapping[str, Any] | None = None,
    price_override: int | None = None,
) -> int:
    """Credits to charge for ``quantity`` of a sink, after price modifiers.

    ``price_override`` replaces the admin-editable sink price (a package override, a paid
    file's price); the sink must still exist and be enabled, and modifiers still apply.
    """
    qty = _credits(quantity, "quantity")
    sink = registry.spend_sinks().get(sink_key)
    price = CreditPrice.objects.filter(sink_key=sink_key).first()
    if price is None and sink is not None:
        sync_registry()
        price = CreditPrice.objects.filter(sink_key=sink_key).first()
    if price is None:
        raise UnknownSink(f"Spend sink {sink_key!r} is not registered.")
    if not price.enabled:
        raise SinkDisabled(f"{price.label} cannot be paid with credits right now.")
    unit = price.credits if price_override is None else _credits(price_override)
    return _apply_price_modifiers(sink_key, unit * qty, qty, context or {})


def _apply_price_modifiers(
    sink_key: str, total: int, quantity: int, context: Mapping[str, Any]
) -> int:
    for modifier in registry.price_modifiers():
        total = max(0, int(modifier(sink_key, total, quantity, context)))
    return total


def spend(
    user: AbstractBaseUser,
    sink_key: str,
    *,
    key: str,
    reference: str = "",
    quantity: int = 1,
    price_override: int | None = None,
) -> LedgerEntry:
    """Charge credits for a registered sink. Atomic and idempotent on ``key``.

    ``price_override`` replaces the sink price (a package ``credit_price``, a paid file's
    price); price modifiers still apply. Raises ``InsufficientCredits``.
    """
    _require_user(user)
    amount = _credits(
        price_of(sink_key, quantity=quantity, price_override=price_override), "price"
    )
    with transaction.atomic():
        wallet = _locked(user)
        entry, _ = _post(
            wallet,
            direction=DEBIT,
            amount=amount,
            reason=LedgerReason.SPEND,
            key=key,
            event_key=sink_key,
            reference=reference,
            snapshot={"quantity": quantity},
        )
    return entry


def refund(entry_key: str, *, reason: str) -> LedgerEntry:
    """Give back a spend (cancelled order, failed delivery). Idempotent."""
    with transaction.atomic():
        original = (
            LedgerEntry.objects.select_related("wallet")
            .filter(key=entry_key, direction=DEBIT, reason=LedgerReason.SPEND)
            .first()
        )
        if original is None:
            raise WalletError(f"No spend with key {entry_key!r} to refund.")
        wallet = _lock(original.wallet_id)[original.wallet_id]
        entry, created = _post(
            wallet,
            direction=CREDIT,
            amount=original.amount,
            reason=LedgerReason.REFUND,
            key=f"refund:{entry_key}"[:200],
            event_key=original.event_key,
            reference=original.reference,
            note=reason[:255],
            enforce_freeze=False,
        )
    if created:
        _notify(
            wallet.user_id,
            "Credits refunded",
            f"{original.amount} credits were returned: {reason}",
        )
    return entry


# ------------------------------------------------------------------------------ escrow


def lock_escrow(
    user: AbstractBaseUser,
    credit_amount: int,
    *,
    key: str,
    reason: str = "",
    reference: str = "",
) -> EscrowHold:
    """Reserve credits (a bounty). Held credits cannot be spent. Idempotent on ``key``."""
    amount = _credits(credit_amount)
    with transaction.atomic():
        wallet = _locked(user)
        existing = EscrowHold.objects.filter(key=key).first()
        if existing is not None:
            if existing.wallet_id != wallet.pk or existing.credits != amount:
                raise IdempotencyConflict(f"Escrow key {key!r} is already in use.")
            return existing
        if wallet.is_frozen:
            raise WalletFrozen("This wallet is frozen.")
        if wallet.available < amount:
            raise InsufficientCredits(amount, wallet.available)
        wallet.held += amount
        wallet.save(update_fields=["held", "updated_at"])
        hold = EscrowHold.objects.create(
            wallet=wallet,
            credits=amount,
            key=key,
            reason=reason[:255],
            reference=reference[:200],
        )
    _publish(
        EventTypes.WALLET_ESCROW_LOCKED,
        {"user_id": wallet.user_id, "credits": amount, "key": key},
    )
    return hold


def _lock_hold(hold: EscrowHold) -> EscrowHold:
    return EscrowHold.objects.select_for_update(of=("self",)).get(pk=hold.pk)


def cancel_escrow(hold: EscrowHold) -> EscrowHold:
    """Return reserved credits to the owner's spendable balance. Idempotent."""
    with transaction.atomic():
        locked = _lock_hold(hold)
        if locked.status == EscrowHold.Status.CANCELLED:
            return locked
        if locked.status != EscrowHold.Status.HOLDING:
            raise EscrowError("This escrow was already released.")
        wallet = _lock(locked.wallet_id)[locked.wallet_id]
        wallet.held -= locked.credits
        wallet.save(update_fields=["held", "updated_at"])
        locked.status = EscrowHold.Status.CANCELLED
        locked.resolved_at = timezone.now()
        locked.save(update_fields=["status", "resolved_at"])
    return locked


def release_escrow(
    hold: EscrowHold,
    *,
    to_user: AbstractBaseUser,
    key: str,
    event_key: str = "bounty.fulfilled",
    reference: str = "",
) -> LedgerEntry:
    """Pay a held amount to another user through ``grant_reward`` (rule and abuse checks).

    The holder is debited and the recipient rewarded in one transaction; a refused
    reward (self-payment, disabled rule) rolls everything back and raises ``EscrowError``.
    """
    _require_user(to_user)
    with transaction.atomic():
        locked = _lock_hold(hold)
        holder_wallet = Wallet.objects.get(pk=locked.wallet_id)
        if locked.status == EscrowHold.Status.RELEASED:
            existing = LedgerEntry.objects.filter(key=key).first()
            if existing is None:
                raise EscrowError("This escrow was released with a different key.")
            return existing
        if locked.status != EscrowHold.Status.HOLDING:
            raise EscrowError("This escrow was cancelled.")
        recipient_wallet = get_wallet(to_user)
        wallets = _lock(holder_wallet.pk, recipient_wallet.pk)
        holder = wallets[holder_wallet.pk]
        _post(
            holder,
            direction=DEBIT,
            amount=locked.credits,
            reason=LedgerReason.ESCROW,
            key=f"{locked.key}:settled"[:200],
            event_key=event_key,
            reference=reference or locked.reference,
            from_held=locked.credits,
            enforce_freeze=False,
        )
        entry = grant_reward(
            to_user,
            event_key,
            key=key,
            reference=reference or locked.reference,
            amount_override=locked.credits,
            context={"source_user_id": holder.user_id},
        )
        if entry is None:
            raise EscrowError("The reward for this escrow was refused.")
        locked.status = EscrowHold.Status.RELEASED
        locked.resolved_at = timezone.now()
        locked.save(update_fields=["status", "resolved_at"])
    _publish(
        EventTypes.WALLET_ESCROW_RELEASED,
        {"to_user_id": to_user.pk, "credits": locked.credits, "key": key},
    )
    return entry


# --------------------------------------------------------------------------- top-ups


def request_topup(user: AbstractBaseUser, credit_amount: int) -> TopUpRequest:
    """Ask for an offline top-up; staff confirm payment, then credits are granted."""
    amount = _credits(credit_amount)
    cfg = get_settings()
    if not cfg.topups_enabled:
        raise TopUpError("Top-ups are switched off.")
    if amount < cfg.min_topup_credits or amount > cfg.max_topup_credits:
        raise TopUpError(
            f"Top up between {cfg.min_topup_credits} and {cfg.max_topup_credits} credits."
        )
    with transaction.atomic():
        wallet = _locked(user)
        open_count = TopUpRequest.objects.filter(
            user_id=wallet.user_id, status=TopUpRequest.Status.PENDING
        ).count()
        if open_count >= cfg.max_open_topups:
            raise TopUpError("You have too many pending top-up requests.")
        request = TopUpRequest.objects.create(
            user_id=wallet.user_id,
            credits=amount,
            price=to_currency(amount),
            currency_label=cfg.currency_label,
        )
        request.order_reference = _create_shop_order(user, request)
        if request.order_reference:
            request.save(update_fields=["order_reference"])
    return request


def _create_shop_order(user: AbstractBaseUser, request: TopUpRequest) -> str:
    """Ask the shop for an order that carries the top-up, when it offers one."""
    from apps.core.app_service import AppService

    shop_api = AppService.get("shop")
    if shop_api is None:
        return ""
    try:
        creator = shop_api.create_credit_topup_order
    except AttributeError:
        return ""
    order = creator(
        user,
        credit_amount=request.credits,
        price=request.price,
        reference=f"wallet:topup:{request.pk}",
    )
    return str(order)


def confirm_topup(
    request_id: int, *, staff_user: AbstractBaseUser | None, reference: str = ""
) -> TopUpRequest:
    """Staff (or the shop's paid-order event) confirm payment; credits granted once."""
    if staff_user is not None:
        _require_perm(staff_user, "wallet.change_topuprequest")
    with transaction.atomic():
        request = TopUpRequest.objects.select_for_update(of=("self",)).get(
            pk=request_id
        )
        if request.status == TopUpRequest.Status.PAID:
            return request
        if request.status != TopUpRequest.Status.PENDING:
            raise TopUpError("Only a pending top-up can be confirmed.")
        user = request.user
        wallet = _locked(user)
        _post(
            wallet,
            direction=CREDIT,
            amount=request.credits,
            reason=LedgerReason.TOPUP,
            key=f"topup:{request.pk}",
            reference=f"topup:{request.pk}",
            note=reference[:255],
            actor=staff_user,
            enforce_freeze=False,
        )
        request.status = TopUpRequest.Status.PAID
        request.payment_reference = reference[:200]
        request.resolved_by_id = staff_user.pk if staff_user is not None else None
        request.resolved_at = timezone.now()
        request.save(
            update_fields=[
                "status",
                "payment_reference",
                "resolved_by_id",
                "resolved_at",
            ]
        )
    _publish(
        WALLET_TOPUP_CONFIRMED_EVENT,
        {
            "user_id": request.user_id,
            "credits": request.credits,
            "topup_id": request.pk,
        },
    )
    _notify(
        request.user_id,
        "Top-up confirmed",
        f"{request.credits} credits were added to your wallet.",
    )
    return request


def cancel_topup(
    request_id: int,
    *,
    user: AbstractBaseUser | None = None,
    staff_user: AbstractBaseUser | None = None,
) -> TopUpRequest:
    """Cancel a pending top-up: the owner may; staff need the change permission."""
    if user is None and staff_user is None:
        raise PermissionDenied("Someone must cancel the top-up.")
    if staff_user is not None:
        _require_perm(staff_user, "wallet.change_topuprequest")
    with transaction.atomic():
        request = TopUpRequest.objects.select_for_update(of=("self",)).get(
            pk=request_id
        )
        if staff_user is None and (user is None or request.user_id != user.pk):
            raise PermissionDenied("This is not your top-up request.")
        if request.status == TopUpRequest.Status.CANCELLED:
            return request
        if request.status != TopUpRequest.Status.PENDING:
            raise TopUpError("Only a pending top-up can be cancelled.")
        request.status = TopUpRequest.Status.CANCELLED
        request.resolved_by_id = staff_user.pk if staff_user is not None else None
        request.resolved_at = timezone.now()
        request.save(update_fields=["status", "resolved_by", "resolved_at"])
    return request


def handle_shop_order_paid(data: Any) -> None:
    """Event-bus handler: a paid shop order that carries a top-up confirms it."""
    if not isinstance(data, Mapping) or data.get("order_id") is None:
        return
    pending = TopUpRequest.objects.filter(
        order_reference=str(data["order_id"]), status=TopUpRequest.Status.PENDING
    ).first()
    if pending is not None:
        confirm_topup(
            pending.pk, staff_user=None, reference=f"shop order {data['order_id']}"
        )


# -------------------------------------------------------------------------- payouts


def _withdrawable(wallet: Wallet) -> int:
    """Earned credits (sales, bounties) not yet paid out, capped by what is spendable."""
    sums = LedgerEntry.objects.filter(wallet=wallet).aggregate(
        earned=Sum(
            "amount", filter=Q(direction=CREDIT, reason__in=WITHDRAWABLE_REASONS)
        ),
        paid=Sum("amount", filter=Q(direction=DEBIT, reason=LedgerReason.PAYOUT)),
        reversed=Sum(
            "amount", filter=Q(direction=CREDIT, reason=LedgerReason.PAYOUT_REVERSAL)
        ),
    )
    earned = int(sums["earned"] or 0)
    paid_out = int(sums["paid"] or 0) - int(sums["reversed"] or 0)
    return max(0, min(wallet.available, earned - paid_out))


def withdrawable_balance(user: AbstractBaseUser) -> int:
    """Credits the user may request as a payout (earned, unpaid, spendable)."""
    _require_user(user)
    wallet = Wallet.objects.filter(user_id=user.pk).first()
    return _withdrawable(wallet) if wallet else 0


def request_payout(
    user: AbstractBaseUser, credit_amount: int, *, method: str, destination: str
) -> PayoutRequest:
    """Ask staff to pay out earned credits. The credits are held (debited) at once."""
    amount = _credits(credit_amount)
    cfg = get_settings()
    if not cfg.payouts_enabled:
        raise PayoutError("Payouts are switched off.")
    if method not in PayoutRequest.Method.values:
        raise PayoutError("Choose a supported payout method.")
    target = destination.strip()
    if not target or len(target) > 255:
        raise PayoutError("A payout destination of up to 255 characters is required.")
    if amount < cfg.min_payout_credits:
        raise PayoutError(f"The minimum payout is {cfg.min_payout_credits} credits.")
    with transaction.atomic():
        wallet = _locked(user)
        if wallet.is_frozen:
            raise WalletFrozen("This wallet is frozen.")
        if PayoutRequest.objects.filter(
            wallet=wallet,
            status__in=(PayoutRequest.Status.PENDING, PayoutRequest.Status.APPROVED),
        ).exists():
            raise PayoutError("Finish your open payout request first.")
        allowed = _withdrawable(wallet)
        if amount > allowed:
            raise InsufficientCredits(amount, allowed)
        fee = cfg.payout_fee_percent
        cash = (to_currency(amount) * (Decimal(100) - fee) / Decimal(100)).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        payout = PayoutRequest.objects.create(
            wallet=wallet,
            credits=amount,
            payout_amount=cash,
            currency_label=cfg.currency_label,
            fee_percent=fee,
            method=method,
            destination=target,
        )
        _post(
            wallet,
            direction=DEBIT,
            amount=amount,
            reason=LedgerReason.PAYOUT,
            key=f"payout:{payout.pk}",
            reference=f"payout:{payout.pk}",
        )
    _publish(
        WALLET_PAYOUT_REQUESTED_EVENT,
        {"user_id": wallet.user_id, "payout_id": payout.pk, "credits": amount},
    )
    return payout


def _payout_for_update(payout_id: int) -> PayoutRequest:
    return PayoutRequest.objects.select_for_update(of=("self",)).get(pk=payout_id)


def approve_payout(payout_id: int, *, staff_user: AbstractBaseUser) -> PayoutRequest:
    _require_perm(staff_user, "wallet.change_payoutrequest")
    with transaction.atomic():
        payout = _payout_for_update(payout_id)
        if payout.status == PayoutRequest.Status.APPROVED:
            return payout
        if payout.status != PayoutRequest.Status.PENDING:
            raise PayoutError("Only a pending payout can be approved.")
        payout.status = PayoutRequest.Status.APPROVED
        payout.reviewed_by_id = staff_user.pk
        payout.reviewed_at = timezone.now()
        payout.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    _resolved(payout, "approved")
    return payout


def mark_payout_paid(
    payout_id: int, *, staff_user: AbstractBaseUser, reference: str = ""
) -> PayoutRequest:
    _require_perm(staff_user, "wallet.change_payoutrequest")
    with transaction.atomic():
        payout = _payout_for_update(payout_id)
        if payout.status == PayoutRequest.Status.PAID:
            return payout
        if payout.status != PayoutRequest.Status.APPROVED:
            raise PayoutError("Approve the payout before marking it paid.")
        payout.status = PayoutRequest.Status.PAID
        payout.payment_reference = reference[:200]
        payout.paid_at = timezone.now()
        payout.save(update_fields=["status", "payment_reference", "paid_at"])
    _resolved(payout, "paid")
    return payout


def reject_payout(
    payout_id: int, *, staff_user: AbstractBaseUser, reason_text: str
) -> PayoutRequest:
    """Reject a pending or approved payout and give the held credits back."""
    _require_perm(staff_user, "wallet.change_payoutrequest")
    note = reason_text.strip()
    if not note:
        raise PayoutError("A reason is required to reject a payout.")
    with transaction.atomic():
        payout = _payout_for_update(payout_id)
        if payout.status == PayoutRequest.Status.REJECTED:
            return payout
        if payout.status not in (
            PayoutRequest.Status.PENDING,
            PayoutRequest.Status.APPROVED,
        ):
            raise PayoutError("A paid payout cannot be rejected.")
        wallet = _lock(payout.wallet_id)[payout.wallet_id]
        _post(
            wallet,
            direction=CREDIT,
            amount=payout.credits,
            reason=LedgerReason.PAYOUT_REVERSAL,
            key=f"payout:{payout.pk}:reversal",
            reference=f"payout:{payout.pk}",
            note=note,
            actor=staff_user,
            enforce_freeze=False,
        )
        payout.status = PayoutRequest.Status.REJECTED
        payout.decision_reason = note[:1000]
        payout.reviewed_by_id = staff_user.pk
        payout.reviewed_at = timezone.now()
        payout.save(
            update_fields=["status", "decision_reason", "reviewed_by", "reviewed_at"]
        )
    _resolved(payout, "rejected")
    return payout


def _resolved(payout: PayoutRequest, outcome: str) -> None:
    user_id = payout.wallet.user_id
    _publish(
        WALLET_PAYOUT_RESOLVED_EVENT,
        {"user_id": user_id, "payout_id": payout.pk, "outcome": outcome},
    )
    _notify(
        user_id,
        f"Payout {outcome}",
        f"Your payout of {payout.credits} credits was {outcome}.",
        "wallet:payouts",
    )


# ------------------------------------------------------------------------------ audit


@dataclass(frozen=True)
class LedgerAudit:
    wallet_id: int
    expected: int
    actual: int
    entries: int

    @property
    def consistent(self) -> bool:
        return self.expected == self.actual


def audit_wallet(wallet: Wallet) -> LedgerAudit:
    """Recompute the balance from the ledger and compare it with the stored total."""
    totals = LedgerEntry.objects.filter(wallet=wallet).aggregate(
        credits=Sum("amount", filter=Q(direction=CREDIT)),
        debits=Sum("amount", filter=Q(direction=DEBIT)),
        entries=Count("id"),
    )
    expected = int(totals["credits"] or 0) - int(totals["debits"] or 0)
    return LedgerAudit(wallet.pk, expected, int(wallet.balance), int(totals["entries"]))
