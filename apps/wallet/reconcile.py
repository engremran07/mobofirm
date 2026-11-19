"""Reconciliation of wallet counters against the ledger (F-020).

``Wallet.balance`` and ``Wallet.held`` are running counters; the ledger rows and the
open escrow holds are the source of truth. This module recomputes the counters and
reports every difference. It is read-only: :func:`reconcile` never changes money.
Fixing a confirmed drift is a separate, explicit, audited act (:func:`repair_wallet`)
that needs a permission, a written reason and an idempotency key, and leaves a
``WalletRepair`` row behind.

A drift candidate is re-checked under the wallet row lock before it is reported, so a
posting that commits between the batch queries is never mistaken for corruption.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Max, Q, Sum

from .models import (
    EscrowHold,
    LedgerEntry,
    Wallet,
    WalletRepair,
    ledger_posting,
)
from .services import WalletError, lock_wallets, require_perm

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500
MAX_ALERTS_PER_RUN = 50
CREDIT = LedgerEntry.Direction.CREDIT
DEBIT = LedgerEntry.Direction.DEBIT


class RepairRefused(WalletError):
    """The drift cannot be repaired from the ledger (nothing to fix, or unsafe)."""


@dataclass(frozen=True)
class WalletDrift:
    """One counter that disagrees with what the ledger (or the holds) say."""

    wallet_id: int
    user_id: int
    field: str
    expected: int
    actual: int

    def as_log_extra(self) -> dict[str, Any]:
        """Structured fields for the alert log (ids and numbers, no personal data)."""
        return {
            "alert": "wallet_reconcile_drift",
            "wallet_id": self.wallet_id,
            "user_id": self.user_id,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class ReconcileReport:
    """Outcome of one reconcile run."""

    wallets_checked: int
    drifts: tuple[WalletDrift, ...]
    complete: bool = True

    @property
    def consistent(self) -> bool:
        """True only for a finished run that found no drift."""
        return self.complete and not self.drifts

    def summary(self) -> dict[str, int | bool]:
        """Counts only; safe for task results and logs."""
        return {
            "wallets_checked": self.wallets_checked,
            "drift_count": len(self.drifts),
            "drifted_wallets": len({drift.wallet_id for drift in self.drifts}),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class RepairPlan:
    """What :func:`repair_wallet` would write for one wallet."""

    wallet_id: int
    previous_balance: int
    repaired_balance: int
    previous_held: int
    repaired_held: int

    @property
    def changes(self) -> bool:
        """Whether the plan would change a counter at all."""
        return (self.previous_balance, self.previous_held) != (
            self.repaired_balance,
            self.repaired_held,
        )


@dataclass(frozen=True)
class LedgerTruth:
    """What the ledger and the holds say one wallet's counters must be."""

    balance: int
    held: int
    last_balance_after: int | None


def ledger_truth(wallet_id: int) -> LedgerTruth:
    """The counters one wallet must have according to its ledger and open holds."""
    totals = LedgerEntry.objects.filter(wallet_id=wallet_id).aggregate(
        credits=Sum("amount", filter=Q(direction=CREDIT)),
        debits=Sum("amount", filter=Q(direction=DEBIT)),
        last_id=Max("id"),
    )
    last = (
        LedgerEntry.objects.filter(pk=totals["last_id"])
        .values_list("balance_after", flat=True)
        .first()
        if totals["last_id"] is not None
        else None
    )
    held = EscrowHold.objects.filter(
        wallet_id=wallet_id, status=EscrowHold.Status.HOLDING
    ).aggregate(total=Sum("credits"))["total"]
    return LedgerTruth(
        balance=int(totals["credits"] or 0) - int(totals["debits"] or 0),
        held=int(held or 0),
        last_balance_after=None if last is None else int(last),
    )


def _drifts(wallet: Wallet, truth: LedgerTruth) -> list[WalletDrift]:
    found: list[WalletDrift] = []

    def add(field: str, expected: int, actual: int) -> None:
        if expected != actual:
            found.append(
                WalletDrift(wallet.pk, wallet.user_id, field, expected, actual)
            )

    add("balance", truth.balance, int(wallet.balance))
    add("held", truth.held, int(wallet.held))
    if truth.last_balance_after is not None:
        add("last_balance_after", int(wallet.balance), truth.last_balance_after)
    return found


def _chain_drift(wallet: Wallet) -> WalletDrift | None:
    """The first ledger row whose ``balance_after`` is not the running total."""
    running = 0
    rows = (
        LedgerEntry.objects.filter(wallet=wallet)
        .order_by("id")
        .values_list("direction", "amount", "balance_after")
        .iterator()
    )
    for direction, amount, balance_after in rows:
        running += amount if direction == CREDIT else -amount
        if running != balance_after:
            return WalletDrift(
                wallet.pk, wallet.user_id, "ledger_chain", running, int(balance_after)
            )
    return None


def _confirmed(wallet_id: int, *, deep: bool) -> list[WalletDrift]:
    """Re-evaluate one wallet under its row lock (no posting can interleave)."""
    with transaction.atomic():
        wallet = lock_wallets(wallet_id).get(wallet_id)
        if wallet is None:
            return []
        found = _drifts(wallet, ledger_truth(wallet_id))
        if deep:
            chain = _chain_drift(wallet)
            if chain is not None:
                found.append(chain)
        return found


def _batches(
    wallet_ids: Iterable[int] | None, batch_size: int
) -> Iterator[list[Wallet]]:
    wanted = None if wallet_ids is None else sorted(set(wallet_ids))
    last = 0
    while True:
        query = Wallet.objects.filter(pk__gt=last).order_by("pk")
        if wanted is not None:
            query = query.filter(pk__in=wanted)
        batch = list(query[:batch_size])
        if not batch:
            return
        yield batch
        last = batch[-1].pk


def batch_ledger_truth(ids: list[int]) -> dict[int, LedgerTruth]:
    """Ledger and hold totals for a batch of wallets in three grouped queries."""
    totals = {
        row["wallet_id"]: row
        for row in LedgerEntry.objects.filter(wallet_id__in=ids)
        .order_by()
        .values("wallet_id")
        .annotate(
            credits=Sum("amount", filter=Q(direction=CREDIT)),
            debits=Sum("amount", filter=Q(direction=DEBIT)),
            last_id=Max("id"),
        )
    }
    last_after = dict(
        LedgerEntry.objects.filter(
            pk__in=[row["last_id"] for row in totals.values()]
        ).values_list("wallet_id", "balance_after")
    )
    holds = {
        row["wallet_id"]: int(row["total"])
        for row in EscrowHold.objects.filter(
            wallet_id__in=ids, status=EscrowHold.Status.HOLDING
        )
        .order_by()
        .values("wallet_id")
        .annotate(total=Sum("credits"))
    }
    truths: dict[int, LedgerTruth] = {}
    for wallet_id in ids:
        row = totals.get(wallet_id)
        after = last_after.get(wallet_id)
        ledger_balance = (
            0 if row is None else int(row["credits"] or 0) - int(row["debits"] or 0)
        )
        truths[wallet_id] = LedgerTruth(
            balance=ledger_balance,
            held=holds.get(wallet_id, 0),
            last_balance_after=None if after is None else int(after),
        )
    return truths


def reconcile(
    *,
    wallet_ids: Iterable[int] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    deep: bool = False,
    max_seconds: float | None = None,
) -> ReconcileReport:
    """Compare every wallet's counters with its ledger. Read-only; never repairs.

    ``deep`` also walks each wallet's ledger and checks every ``balance_after``.
    ``max_seconds`` stops between batches and marks the report incomplete, so a
    scheduled run always ends inside its time limit.
    """
    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    checked = 0
    drifts: list[WalletDrift] = []
    for batch in _batches(wallet_ids, max(1, batch_size)):
        if deadline is not None and time.monotonic() > deadline:
            return ReconcileReport(checked, tuple(drifts), complete=False)
        truths = batch_ledger_truth([wallet.pk for wallet in batch])
        for wallet in batch:
            suspicious = bool(_drifts(wallet, truths[wallet.pk])) or deep
            if suspicious:
                drifts.extend(_confirmed(wallet.pk, deep=deep))
            checked += 1
    return ReconcileReport(checked, tuple(drifts))


def log_report(report: ReconcileReport) -> None:
    """Structured alert logging: one ERROR per drift (capped) plus a run summary."""
    for drift in report.drifts[:MAX_ALERTS_PER_RUN]:
        logger.error("wallet reconcile drift", extra=drift.as_log_extra())
    if report.drifts:
        logger.error(
            "wallet reconcile found drift",
            extra={"alert": "wallet_reconcile_drift_summary", **report.summary()},
        )
    elif not report.complete:
        logger.warning(
            "wallet reconcile stopped before finishing",
            extra={"alert": "wallet_reconcile_incomplete", **report.summary()},
        )
    else:
        logger.info("wallet reconcile clean", extra=report.summary())


def plan_repair(wallet_id: int) -> RepairPlan:
    """What a repair would write now (read-only); ``RepairRefused`` when unsafe."""
    wallet = Wallet.objects.filter(pk=wallet_id).first()
    if wallet is None:
        raise RepairRefused(f"Wallet {wallet_id} does not exist.")
    return _plan(wallet, ledger_truth(wallet_id))


def _plan(wallet: Wallet, truth: LedgerTruth) -> RepairPlan:
    if truth.balance < 0:
        raise RepairRefused(
            "The ledger itself is inconsistent (credits minus debits is negative); "
            "investigate the ledger rows, a counter repair cannot fix that."
        )
    if truth.held > truth.balance:
        raise RepairRefused("The open escrow holds exceed the ledger balance.")
    plan = RepairPlan(
        wallet.pk, int(wallet.balance), truth.balance, int(wallet.held), truth.held
    )
    if not plan.changes:
        raise RepairRefused("The wallet counters already match the ledger.")
    return plan


def repair_wallet(
    wallet_id: int,
    *,
    actor: AbstractBaseUser,
    reason_text: str,
    key: str,
) -> WalletRepair:
    """Rewrite a drifted wallet's counters from the ledger, with an audit record.

    Needs ``wallet.adjust_wallet`` and a written reason. Idempotent on ``key`` (a second
    delivery returns the first record). The ledger is never modified: it is the truth.
    """
    require_perm(actor, "wallet.adjust_wallet")
    note = reason_text.strip()
    if not note:
        raise WalletError("A reason is required for a repair.")
    if not key or len(key) > 200:
        raise WalletError("A repair key of 1-200 characters is required.")
    with transaction.atomic():
        existing = WalletRepair.objects.filter(key=key).first()
        if existing is not None:
            if existing.wallet_id != wallet_id:
                raise WalletError(f"Key {key!r} was already used for another wallet.")
            return existing
        wallet = lock_wallets(wallet_id).get(wallet_id)
        if wallet is None:
            raise RepairRefused(f"Wallet {wallet_id} does not exist.")
        plan = _plan(wallet, ledger_truth(wallet_id))
        wallet.balance = plan.repaired_balance
        wallet.held = plan.repaired_held
        with ledger_posting():
            wallet.save(update_fields=["balance", "held", "updated_at"])
        record = WalletRepair.objects.create(
            wallet=wallet,
            previous_balance=plan.previous_balance,
            repaired_balance=plan.repaired_balance,
            previous_held=plan.previous_held,
            repaired_held=plan.repaired_held,
            reason=note[:255],
            key=key,
            actor_id=actor.pk,
        )
    logger.warning(
        "wallet counters repaired from the ledger",
        extra={
            "alert": "wallet_repaired",
            "wallet_id": wallet_id,
            "actor_id": actor.pk,
            "previous_balance": plan.previous_balance,
            "repaired_balance": plan.repaired_balance,
            "previous_held": plan.previous_held,
            "repaired_held": plan.repaired_held,
        },
    )
    return record
