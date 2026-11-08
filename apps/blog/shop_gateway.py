"""Lazy seam to the shop's featured-blog contract (``apps/shop/api.py``).

The shop owns packages, entitlements and purchases; the blog owns posts. The blog
reaches the shop only through ``AppService.get("shop")`` and renders the teaser when
the shop is absent, switched off or has not published the contract.

Contract (decided with the shop):

* ``featured_blog_access(user, post_id, price_usd) -> BlogAccess``
* ``purchase_featured_blog(user, post_id, price_usd, key) -> BlogAccess``
  (atomic, idempotent on ``key``; the shop records the purchase and spends the
  derived credits through the wallet sink ``blog.featured_purchase``)
* optional extras for listings: ``featured_blog_buyers(post_id)`` and
  ``purchased_featured_blog_ids(user)``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from apps.core.app_service import AppService

UNAVAILABLE_REASON = "Premium articles are not available on this site right now."


@dataclass(frozen=True, slots=True)
class BlogAccess:
    """The shop's answer: may this user read the post, and what would it cost."""

    allowed: bool
    via: str = ""
    credit_price: int | None = None
    reason: str = ""


DENIED = BlogAccess(False, "unavailable", None, UNAVAILABLE_REASON)


def _function(name: str) -> Callable[..., Any] | None:
    module = AppService.get("shop")
    candidate = getattr(module, name, None) if module is not None else None
    return candidate if callable(candidate) else None


def available() -> bool:
    return (
        _function("featured_blog_access") is not None
        and _function("purchase_featured_blog") is not None
    )


def _coerce(result: Any) -> BlogAccess:
    credit_price = getattr(result, "credit_price", None)
    return BlogAccess(
        allowed=bool(getattr(result, "allowed", False)),
        via=str(getattr(result, "via", "") or ""),
        credit_price=int(credit_price) if credit_price is not None else None,
        reason=str(getattr(result, "reason", "") or ""),
    )


def featured_blog_access(
    user: Any, post_id: int, price_usd: Decimal | None
) -> BlogAccess:
    """Whether ``user`` may read the post body through a package or an earlier purchase."""
    function = _function("featured_blog_access")
    if function is None:
        return DENIED
    return _coerce(function(user, post_id, price_usd))


def purchase_featured_blog(
    user: Any, post_id: int, price_usd: Decimal | None, key: str
) -> BlogAccess:
    """Buy the post with credits; idempotent on ``key``."""
    function = _function("purchase_featured_blog")
    if function is None:
        return DENIED
    return _coerce(function(user, post_id, price_usd, key))


def buyers(post_id: int) -> list[dict[str, Any]]:
    """Read-only buyer rows for the admin; empty when the shop cannot list them."""
    function = _function("featured_blog_buyers")
    if function is None:
        return []
    return [dict(row) for row in function(post_id)]


def purchased_post_ids(user: Any) -> list[int]:
    """Posts ``user`` bought, newest first; empty when the shop cannot list them."""
    function = _function("purchased_featured_blog_ids")
    if function is None:
        return []
    return [int(post_id) for post_id in function(user)]
