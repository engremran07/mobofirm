"""Account-erasure contract of the wallet (F-041).

The ledger is accounting data: its rows, amounts and running balances are kept so every
balance still reconciles. What is removed is the *personal* free text around them
(staff notes, payment receipts, payout destinations) and the link to a staff actor.
Nothing is deleted; ``Wallet.user`` stays ``PROTECT`` and the ``users`` app anonymises
the account row itself in place.

Contract for the ``users`` erasure workflow (``apps.wallet.api``):

1. ``erasure_blockers(user)`` lists reasons the account cannot be erased yet (an open
   payout, credits held in escrow). Resolve them first.
2. ``anonymise_user(user)`` scrubs the wallet's personal data in one transaction. It is
   idempotent: running it again changes nothing and reports zeros.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from .models import (
    EscrowHold,
    LedgerEntry,
    PayoutRequest,
    RewardRefusal,
    TopUpRequest,
    Wallet,
)
from .services import WalletError, audit_wallet, lock_wallets

ERASED = "[erased]"


class ErasureBlocked(WalletError):
    """The wallet still has an obligation that needs the personal data."""


@dataclass(frozen=True)
class ErasureReport:
    """Counts only (never personal data), as the ``users`` erasure receipt expects.

    ``unreconciled_wallets`` is 1 when the wallet's counter no longer equals its ledger
    after the scrub: erasure never repairs money and never refuses because of a drift.
    """

    ledger_notes: int = 0
    ledger_actors: int = 0
    topup_references: int = 0
    staff_links: int = 0
    payout_records: int = 0
    refusal_details: int = 0
    frozen_reasons: int = 0
    unreconciled_wallets: int = 0

    @property
    def changed(self) -> int:
        """Total number of rows that were scrubbed or unlinked."""
        return (
            self.ledger_notes
            + self.ledger_actors
            + self.topup_references
            + self.staff_links
            + self.payout_records
            + self.refusal_details
            + self.frozen_reasons
        )


def erasure_blockers(user: AbstractBaseUser) -> tuple[str, ...]:
    """Human-readable reasons the user's wallet data cannot be erased yet."""
    wallet = Wallet.objects.filter(user_id=user.pk).first()
    if wallet is None:
        return ()
    blockers: list[str] = []
    open_payouts = PayoutRequest.objects.filter(
        wallet=wallet,
        status__in=(PayoutRequest.Status.PENDING, PayoutRequest.Status.APPROVED),
    ).count()
    if open_payouts:
        blockers.append(f"{open_payouts} payout request(s) still open")
    holds = EscrowHold.objects.filter(
        wallet=wallet, status=EscrowHold.Status.HOLDING
    ).count()
    if holds:
        blockers.append(f"{holds} escrow hold(s) still holding credits")
    return tuple(blockers)


def anonymise_user(user: AbstractBaseUser) -> ErasureReport:
    """Scrub personal data from the user's wallet records; balances are untouched.

    Raises ``ErasureBlocked`` while :func:`erasure_blockers` is not empty. Also unlinks
    the user as the staff actor of other wallets' rows (a staff account being erased).
    """
    blockers = erasure_blockers(user)
    if blockers:
        raise ErasureBlocked("; ".join(blockers))
    with transaction.atomic():
        wallet = Wallet.objects.filter(user_id=user.pk).first()
        if wallet is not None:
            wallet = lock_wallets(wallet.pk)[wallet.pk]
        notes = actors = 0
        topups = payouts = refusals = frozen = 0
        if wallet is not None:
            notes = (
                LedgerEntry.objects.filter(wallet=wallet)
                .exclude(note="")
                .redact(note="")
            )
            topups = (
                TopUpRequest.objects.filter(user_id=user.pk)
                .exclude(payment_reference="")
                .update(payment_reference="")
            )
            payouts = (
                PayoutRequest.objects.filter(wallet=wallet)
                .exclude(destination=ERASED, payment_reference="", decision_reason="")
                .update(destination=ERASED, payment_reference="", decision_reason="")
            )
            refusals = (
                RewardRefusal.objects.filter(user_id=user.pk)
                .exclude(detail="")
                .update(detail="")
            )
            frozen = (
                Wallet.objects.filter(pk=wallet.pk)
                .exclude(frozen_reason="")
                .update(frozen_reason="")
            )
        actors = LedgerEntry.objects.filter(actor_id=user.pk).redact(actor=None)
        staff_links = TopUpRequest.objects.filter(resolved_by_id=user.pk).update(
            resolved_by=None
        ) + PayoutRequest.objects.filter(reviewed_by_id=user.pk).update(
            reviewed_by=None
        )
        unreconciled = 0
        if wallet is not None:
            unreconciled = 0 if audit_wallet(wallet).consistent else 1
    return ErasureReport(
        ledger_notes=notes,
        ledger_actors=actors,
        topup_references=topups,
        staff_links=staff_links,
        payout_records=payouts,
        refusal_details=refusals,
        frozen_reasons=frozen,
        unreconciled_wallets=unreconciled,
    )
