"""Lazy seam to the credits economy (``apps.wallet.api``) for device slot resets.

Devices never keeps a price or balance: the wallet owns both. When the wallet is not
installed, or the sink is switched off by staff, :func:`price` returns ``None`` and the
UI says plainly that paying with credits is unavailable.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

from django.apps import apps as django_apps

WALLET_APP = "apps.wallet"
WALLET_API = "apps.wallet.api"
SLOT_RESET_SINK = "device.slot_reset"
SLOT_RESET_LABEL = "Reset registered-browser slots"
SLOT_RESET_DEFAULT_CREDITS = 10


class CreditsUnavailable(Exception):
    """The wallet is missing or the slot-reset sink is disabled or unknown."""


class InsufficientCreditsError(Exception):
    """The balance cannot cover the price."""

    def __init__(self, needed: int, have: int) -> None:
        super().__init__(f"{needed} credits needed, {have} available.")
        self.needed = needed
        self.available = have


def _api() -> ModuleType | None:
    if not django_apps.is_installed(WALLET_APP):
        return None
    return import_module(WALLET_API)


def register_sink() -> bool:
    """Declare ``device.slot_reset`` to the wallet; False when there is no wallet."""
    wallet = _api()
    if wallet is None:
        return False
    wallet.register_spend_sink(
        wallet.SpendSink(
            key=SLOT_RESET_SINK,
            label=SLOT_RESET_LABEL,
            app="devices",
            default_credits=SLOT_RESET_DEFAULT_CREDITS,
            user_price=price,
        )
    )
    return True


def available() -> bool:
    return _api() is not None


def price(user: Any = None) -> int | None:
    """Credits one reset costs ``user`` now, or ``None`` when credits cannot be used.

    Resolution: the user's package override (``PackageTier.slot_reset_credits``, via
    the shop's effective entitlements), else the global ``device.slot_reset`` price
    staff edit under Credits economy → Prices. Price modifiers (campaigns) apply to both.
    """
    wallet = _api()
    if wallet is None:
        return None
    try:
        return int(
            wallet.price_of(SLOT_RESET_SINK, price_override=package_override(user))
        )
    except (wallet.UnknownSink, wallet.SinkDisabled):
        return None


def package_override(user: Any) -> int | None:
    """The slot-reset credits the user's effective package sets, or ``None``."""
    if user is None or not django_apps.is_installed("apps.shop"):
        return None
    from apps.shop.entitlements import effective_entitlements

    return effective_entitlements(user).slot_reset_credits


def balance(user: Any) -> int:
    wallet = _api()
    return 0 if wallet is None else int(wallet.balance(user))


def earn_url() -> str:
    wallet = _api()
    return "" if wallet is None else str(wallet.earn_url())


def spend(user: Any, *, key: str, reference: str) -> int:
    """Charge one reset idempotently on ``key``; returns the credits charged."""
    wallet = _api()
    if wallet is None:
        raise CreditsUnavailable("Credits are not available on this site.")
    try:
        entry = wallet.spend(
            user,
            SLOT_RESET_SINK,
            key=key,
            reference=reference,
            price_override=package_override(user),
        )
    except wallet.InsufficientCredits as error:
        raise InsufficientCreditsError(
            int(error.needed), int(error.available)
        ) from error
    except (wallet.UnknownSink, wallet.SinkDisabled, wallet.WalletFrozen) as error:
        raise CreditsUnavailable(str(error)) from error
    return int(entry.amount)


def refund(key: str, *, reason: str) -> None:
    wallet = _api()
    if wallet is not None:
        wallet.refund(key, reason=reason)
