"""Lazy seam to promotional package prices (``apps.promotions.api``).

When the promotions app is missing or switched off nothing is discounted and every
purchase flow keeps charging the package's own prices. Promotions never keep prices
of their own beyond the campaign's discounted figures; the shop stays the source of
truth for the regular price.
"""

from __future__ import annotations

from typing import Any

from apps.core.app_service import AppService


def quote(user: Any, package: Any) -> Any | None:
    """The live ``PriceQuote`` of ``package`` for ``user``, or ``None``."""
    api = AppService.get("promotions")
    if api is None:
        return None
    return api.effective_price(user, package)


def redeem(user: Any, package: Any, *, reference: str) -> None:
    """Record that the promotional price was used for this order (idempotent)."""
    api = AppService.get("promotions")
    if api is not None:
        api.redeem_price(user, package, reference=reference)
