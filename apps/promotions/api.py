"""Public contract of the promotions app (use ``AppService.get("promotions")``).

* :func:`effective_price` / :func:`redeem_price` – promotional package prices (shop).
* :func:`offers_for_user` – running offers for panels such as My files and "Ways to earn".
* :func:`offer_url` – the offer landing page of a promotion.

Nothing here keeps balances: bonus quota flows through the shop entitlement resolver,
reward boosts through the wallet reward modifier and bonus credits through the wallet.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from .offers import OfferCard, OfferState, offer_state, offers_for_user
from .pricing import PriceQuote, effective_price, redeem


def offer_url(slug: str) -> str:
    return reverse("promotions:offer", kwargs={"slug": slug})


def index_url() -> str:
    return reverse("promotions:index")


def redeem_price(user: Any, package: Any, *, reference: str) -> bool:
    """Record that ``user`` paid the promotional price of ``package`` (once per order)."""
    return redeem(user, package, reference=reference)


__all__ = [
    "OfferCard",
    "OfferState",
    "PriceQuote",
    "effective_price",
    "index_url",
    "offer_state",
    "offer_url",
    "offers_for_user",
    "redeem_price",
]
