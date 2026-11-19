"""Public contract of the credits economy. Other apps import only from here.

Earn:  ``register_reward_event`` (in ``apps/<app>/reward_events.py``), ``grant_reward``,
       ``register_reward_modifier``.
Spend: ``register_spend_sink`` (in ``apps/<app>/spend_sinks.py``), ``price_of``,
       ``spend``, ``refund``, ``register_price_modifier``.
Ledger: ``balance``, ``withdrawable_balance``, ``credit``, ``debit``, escrow and
        payout helpers. All credits are whole ``int`` numbers.
Rules:  ``reward_rules`` (read-only ``RewardRuleView`` copies for display).
Integrity: ``reconcile`` (read-only drift report; repairs are staff/ops only).
Erasure: ``erasure_blockers`` and ``anonymise_user`` (the ``users`` erasure workflow
        calls these; balances and ledger amounts are never touched).
"""

from __future__ import annotations

from django.urls import reverse

from .erasure import ErasureBlocked, ErasureReport, anonymise_user, erasure_blockers
from .models import LedgerReason
from .reconcile import ReconcileReport, WalletDrift, reconcile
from .registry import (
    RewardEvent,
    SpendSink,
    register_price_modifier,
    register_reward_event,
    register_reward_modifier,
    register_spend_sink,
)
from .services import (
    EscrowError,
    IdempotencyConflict,
    InsufficientCredits,
    InvalidAmount,
    PayoutError,
    RewardRuleView,
    SinkDisabled,
    TopUpError,
    UnknownSink,
    WalletError,
    WalletFrozen,
    balance,
    balance_snapshot,
    cancel_escrow,
    credit,
    credits_per_currency,
    debit,
    format_currency,
    grant_reward,
    lock_escrow,
    price_label,
    price_of,
    refund,
    release_escrow,
    request_payout,
    request_topup,
    reward_rules,
    rewards_earned,
    spend,
    to_credits,
    to_currency,
    withdrawable_balance,
)


def earn_url() -> str:
    """The "Ways to earn" page."""
    return reverse("wallet:earn")


def spend_url() -> str:
    """The "Ways to spend" page."""
    return reverse("wallet:spend")


__all__ = [
    "ErasureBlocked",
    "ErasureReport",
    "EscrowError",
    "IdempotencyConflict",
    "InsufficientCredits",
    "InvalidAmount",
    "LedgerReason",
    "PayoutError",
    "ReconcileReport",
    "RewardEvent",
    "RewardRuleView",
    "SinkDisabled",
    "SpendSink",
    "TopUpError",
    "UnknownSink",
    "WalletDrift",
    "WalletError",
    "WalletFrozen",
    "anonymise_user",
    "balance",
    "balance_snapshot",
    "cancel_escrow",
    "credit",
    "credits_per_currency",
    "debit",
    "earn_url",
    "erasure_blockers",
    "format_currency",
    "grant_reward",
    "lock_escrow",
    "price_label",
    "price_of",
    "reconcile",
    "refund",
    "register_price_modifier",
    "register_reward_event",
    "register_reward_modifier",
    "register_spend_sink",
    "release_escrow",
    "request_payout",
    "request_topup",
    "reward_rules",
    "rewards_earned",
    "spend",
    "spend_url",
    "to_credits",
    "to_currency",
    "withdrawable_balance",
]
