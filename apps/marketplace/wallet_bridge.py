"""The only place marketplace touches ``apps.wallet`` (lazy, optional).

Contract (``apps.wallet.services``): ``balance(user)``, ``withdrawable_balance(user)``,
``to_credits(amount)``, ``credit``/``debit(user, credits, *, reason, key, reference,
note)`` idempotent per ``key``, ``request_payout(user, credits, *, method,
destination)`` and the ``InsufficientCredits`` / ``PayoutError`` exceptions. Credits
are whole numbers. When the wallet app is not installed every function reports that
honestly instead of pretending to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from importlib import import_module
from typing import Any

from django.apps import apps

WALLET_APP = "apps.wallet"
WALLET_SERVICES = "apps.wallet.services"
WALLET_MODELS = "apps.wallet.models"


class WalletUnavailableError(RuntimeError):
    """The credit wallet is not installed, so money movements are impossible."""


class WalletRejectedError(ValueError):
    """The wallet refused the movement (insufficient credits, frozen, bad input)."""


@dataclass(frozen=True)
class WalletBalances:
    balance: int
    withdrawable: int


def wallet_enabled() -> bool:
    return apps.is_installed(WALLET_APP)


def _services() -> Any:
    if not wallet_enabled():
        raise WalletUnavailableError("The credit wallet is not enabled.")
    return import_module(WALLET_SERVICES)


def _rejections(module: Any) -> tuple[type[Exception], ...]:
    names = ("InsufficientCredits", "PayoutError", "WalletFrozen")
    found = tuple(
        error
        for name in names
        if isinstance(error := getattr(module, name, None), type)
        and issubclass(error, Exception)
    )
    return (*found, ValueError)


def _reason(module: Any, *names: str) -> Any:
    reasons = getattr(module, "LedgerReason", None)
    if reasons is None:
        reasons = import_module(WALLET_MODELS).LedgerReason
    for name in names:
        if hasattr(reasons, name):
            return getattr(reasons, name)
    raise WalletUnavailableError("The wallet has no ledger reason for earnings.")


def balances(user: Any) -> WalletBalances | None:
    """Spendable and withdrawable credits, or ``None`` when there is no wallet."""
    if not wallet_enabled():
        return None
    module = _services()
    return WalletBalances(
        balance=int(module.balance(user)),
        withdrawable=int(module.withdrawable_balance(user)),
    )


def to_credits(amount: Decimal) -> int:
    """Convert a currency amount using the wallet's configured rate."""
    return int(_services().to_credits(amount))


def credit_earning(
    user: Any, amount: int, *, key: str, reference: str, note: str
) -> None:
    module = _services()
    try:
        module.credit(
            user,
            amount,
            reason=_reason(module, "EARNING"),
            key=key,
            reference=reference,
            note=note,
        )
    except _rejections(module) as exc:
        raise WalletRejectedError(str(exc)) from exc


def reverse_earning(
    user: Any, amount: int, *, key: str, reference: str, note: str
) -> None:
    module = _services()
    try:
        module.debit(
            user,
            amount,
            reason=_reason(module, "REFUND", "EARNING"),
            key=key,
            reference=reference,
            note=note,
        )
    except _rejections(module) as exc:
        raise WalletRejectedError(str(exc)) from exc


def request_payout(user: Any, amount: int, *, method: str, destination: str) -> None:
    module = _services()
    try:
        module.request_payout(user, amount, method=method, destination=destination)
    except _rejections(module) as exc:
        raise WalletRejectedError(str(exc)) from exc
