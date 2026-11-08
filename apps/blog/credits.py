"""Lazy seam to the wallet for display only (prices and balances).

Charging goes through the shop's ``purchase_featured_blog``; this module only
converts the staff-approved USD price with the wallet's single rate and reads the
balance for the buy dialog. Without the wallet every helper returns ``None``.
"""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from types import ModuleType
from typing import Any

from django.apps import apps

from apps.core.app_service import app_enabled


def _wallet() -> ModuleType | None:
    if not apps.is_installed("apps.wallet") or not app_enabled("wallet"):
        return None
    return import_module("apps.wallet.api")


def credits_for(price_usd: Decimal | None) -> int | None:
    """The one conversion: USD price to whole credits at the wallet rate."""
    wallet = _wallet()
    if wallet is None or price_usd is None:
        return None
    return int(wallet.to_credits(price_usd))


def balance_of(user: Any) -> int | None:
    wallet = _wallet()
    if wallet is None or not getattr(user, "is_authenticated", False):
        return None
    return int(wallet.balance(user))


def earn_url() -> str:
    wallet = _wallet()
    return str(wallet.earn_url()) if wallet is not None else ""
