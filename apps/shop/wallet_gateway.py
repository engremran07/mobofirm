"""Lazy seam to the wallet's public API (``apps.wallet.api``).

The wallet is the one credits engine; shop never keeps balances or prices of its own.
When the wallet is not installed, or has not published the contract yet, credits are
simply unavailable (:func:`available` is false) and every cash flow keeps working.
Nothing here imports shop models, so ``services`` can use it without cycles.
"""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from types import ModuleType
from typing import Any

from django.apps import apps as django_apps

WALLET_APP = "apps.wallet"
WALLET_API = "apps.wallet.api"
CONTRACT = (
    "SpendSink",
    "register_spend_sink",
    "spend",
    "refund",
    "balance",
    "to_credits",
    "earn_url",
    "InsufficientCredits",
    "WalletError",
)


class CreditsUnavailable(Exception):
    """The wallet is not installed or does not publish the credits contract."""


class InsufficientCreditsError(Exception):
    """The wallet balance cannot cover the price."""

    def __init__(self, needed: int, balance_available: int) -> None:
        super().__init__(f"{needed} credits needed, {balance_available} available.")
        self.needed = needed
        self.available = balance_available


def _api() -> ModuleType | None:
    if not django_apps.is_installed(WALLET_APP):
        return None
    try:
        module = import_module(WALLET_API)
    except ModuleNotFoundError as error:
        if error.name in {WALLET_APP, WALLET_API}:
            return None
        raise
    return module if all(hasattr(module, name) for name in CONTRACT) else None


def available() -> bool:
    return _api() is not None


def _require() -> ModuleType:
    module = _api()
    if module is None:
        raise CreditsUnavailable("Credits are not available on this site right now.")
    return module


def balance(user: Any) -> int:
    return int(_require().balance(user))


def to_credits(amount: Decimal) -> int:
    return int(_require().to_credits(amount))


def price_label(amount: Decimal) -> str:
    """``$4.00 · 40 credits``; the dollar amount alone when credits are unavailable."""
    module = _api()
    if module is None:
        return f"${Decimal(amount):.2f}"
    try:
        label = module.price_label
    except AttributeError:
        return f"${Decimal(amount):.2f} · {module.to_credits(amount)} credits"
    return str(label(amount))


def earn_url() -> str:
    return str(_require().earn_url())


def spend(user: Any, sink: str, *, key: str, reference: str, amount: int) -> None:
    """Charge ``amount`` credits idempotently on ``key``; raises InsufficientCreditsError."""
    wallet = _require()
    try:
        wallet.spend(user, sink, key=key, reference=reference, price_override=amount)
    except wallet.InsufficientCredits as error:
        raise InsufficientCreditsError(
            int(getattr(error, "needed", amount)),
            int(getattr(error, "available", 0)),
        ) from error
    except wallet.WalletError as error:
        raise CreditsUnavailable(str(error)) from error


def refund(key: str, *, reason: str) -> None:
    """Reverse the spend recorded under ``key`` (idempotent in the wallet)."""
    _require().refund(key, reason=reason)


def register_sink(
    key: str, label: str, default_credits: int = 0, *, derived: bool = False
) -> bool:
    """Declare a spend sink; returns False when the wallet is unavailable."""
    wallet = _api()
    if wallet is None:
        return False
    wallet.register_spend_sink(
        wallet.SpendSink(
            key=key,
            label=label,
            app="shop",
            default_credits=default_credits,
            derived=derived,
        )
    )
    return True
