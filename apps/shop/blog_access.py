"""Featured blog articles: who may read one in full, and buying one with credits.

The blog owns posts; the shop owns packages, purchases and the credits spend. The blog
calls this through ``apps.shop.api`` (``featured_blog_access`` /
``purchase_featured_blog``) and passes the staff-approved USD price. Credits are derived
from it with the wallet's one rate, never stored on the post.

A reader gets the full body through, in this order:

1. an earlier credit purchase of the post (lifetime);
2. an earlier unlock from their package quota (counted once per article, lifetime);
3. a package that includes featured blogs and still has room in its window: the
   article is unlocked from the package and **nothing is charged**.

Everyone else is denied and offered the credits price. A package holder is never
charged for an article their package covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.db import IntegrityError, transaction

from . import wallet_gateway
from .entitlements import effective_entitlements
from .models import FeaturedBlogPurchase, FeaturedBlogUnlock
from .services import lock_user

BLOG_SINK = "blog.featured_purchase"
VIA_PURCHASE = "purchase"
VIA_PACKAGE = "package"


@dataclass(frozen=True, slots=True)
class BlogAccess:
    """May this user read the article in full, and what would it cost in credits."""

    allowed: bool
    via: str = ""
    credit_price: int | None = None
    reason: str = ""


def _authenticated(user: Any) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False))


def _credit_price(price_usd: Decimal | None) -> int | None:
    """Credits for the dollar price at the wallet rate; ``None`` when not for sale."""
    if price_usd is None or price_usd <= 0 or not wallet_gateway.available():
        return None
    return wallet_gateway.to_credits(price_usd)


def _allowed(via: str) -> BlogAccess:
    return BlogAccess(True, via)


def _owned(user: Any, post_id: int) -> BlogAccess | None:
    if FeaturedBlogPurchase.objects.filter(user=user, post_id=post_id).exists():
        return _allowed(VIA_PURCHASE)
    if FeaturedBlogUnlock.objects.filter(user=user, post_id=post_id).exists():
        return _allowed(VIA_PACKAGE)
    return None


def _unlock_from_package(user: Any, post_id: int) -> BlogAccess | None:
    """Unlock from the package quota; ``None`` when the package does not cover it.

    Returns a denial (``via="quota"``) when the package covers featured blogs but its
    window allowance is used up. Runs under the user lock so two requests cannot
    both take the last unit.
    """
    entitled = effective_entitlements(user)
    if not entitled.featured_blog:
        return None
    with transaction.atomic():
        lock_user(user)
        if FeaturedBlogUnlock.objects.filter(user=user, post_id=post_id).exists():
            return _allowed(VIA_PACKAGE)
        limit = entitled.featured_blog_limit
        if limit:
            used = FeaturedBlogUnlock.objects.filter(
                user=user, unlocked_at__gte=entitled.window_start
            ).count()
            if used >= limit:
                return BlogAccess(
                    False,
                    "quota",
                    None,
                    f"Your package includes {limit} featured "
                    f"article{'' if limit == 1 else 's'} per period and they are "
                    "used up.",
                )
        FeaturedBlogUnlock.objects.create(user=user, post_id=post_id)
    return _allowed(VIA_PACKAGE)


def _denied(reason: str, via: str, price_usd: Decimal | None) -> BlogAccess:
    return BlogAccess(False, via, _credit_price(price_usd), reason)


def featured_blog_access(
    user: Any, post_id: int, price_usd: Decimal | None
) -> BlogAccess:
    """Whether ``user`` may read the featured article ``post_id`` in full right now.

    A package holder's first read unlocks the article from the package quota (counted
    once); an earlier purchase or unlock is returned as is.
    """
    if not _authenticated(user):
        return _denied("Sign in to read featured articles.", "anonymous", price_usd)
    owned = _owned(user, post_id)
    if owned is not None:
        return owned
    unlocked = _unlock_from_package(user, post_id)
    if unlocked is None:
        return _denied(
            "Featured articles come with a package, or you can buy this one with "
            "credits.",
            "none",
            price_usd,
        )
    if unlocked.allowed:
        return unlocked
    return BlogAccess(False, unlocked.via, _credit_price(price_usd), unlocked.reason)


def purchase_featured_blog(
    user: Any, post_id: int, price_usd: Decimal | None, key: str
) -> BlogAccess:
    """Buy the article with credits. Atomic and idempotent on ``key``.

    Already owned, or covered by the user's package: allowed with no charge. Otherwise
    the derived credits are spent through the wallet and the purchase is recorded in
    the same transaction, so a failure after the spend rolls the spend back too.
    """
    if not _authenticated(user):
        return _denied("Sign in to buy this article.", "anonymous", price_usd)
    price_credits = _credit_price(price_usd)
    if price_credits is None:
        return BlogAccess(
            False, "unavailable", None, "This article cannot be bought with credits."
        )
    try:
        return _purchase(user, post_id, price_credits, key)
    except IntegrityError:
        # A concurrent request recorded the same purchase first: that one wins.
        owned = _owned(user, post_id)
        if owned is not None:
            return owned
        raise


def _purchase(user: Any, post_id: int, price_credits: int, key: str) -> BlogAccess:
    existing = FeaturedBlogPurchase.objects.filter(key=key).first()
    if existing is not None:
        if existing.user_id != user.pk:
            return BlogAccess(
                False, "conflict", price_credits, "This request key is taken."
            )
        return _allowed(VIA_PURCHASE)
    owned = _owned(user, post_id)
    if owned is not None:
        return owned
    unlocked = _unlock_from_package(user, post_id)
    if unlocked is not None and unlocked.allowed:
        return unlocked
    with transaction.atomic():
        lock_user(user)
        try:
            wallet_gateway.spend(
                user,
                BLOG_SINK,
                key=f"{BLOG_SINK}:{key}",
                reference=f"blog:{post_id}",
                amount=price_credits,
            )
        except wallet_gateway.InsufficientCreditsError as error:
            return BlogAccess(
                False,
                "insufficient",
                price_credits,
                f"You need {error.needed - error.available} more credits.",
            )
        except wallet_gateway.CreditsUnavailable as error:
            return BlogAccess(False, "unavailable", price_credits, str(error))
        FeaturedBlogPurchase.objects.create(
            user=user, post_id=post_id, credits=price_credits, key=key
        )
    return _allowed(VIA_PURCHASE)


def featured_blog_buyers(post_id: int) -> list[dict[str, Any]]:
    """Read-only buyer rows for the blog admin, newest first."""
    rows = FeaturedBlogPurchase.objects.filter(post_id=post_id).select_related("user")
    return [
        {
            "user_id": purchase.user_id,
            "user": str(purchase.user),
            "credits": purchase.credits,
            "created_at": purchase.created_at,
        }
        for purchase in rows
    ]


def purchased_featured_blog_ids(user: Any) -> list[int]:
    """Posts ``user`` bought with credits, newest first."""
    if not _authenticated(user):
        return []
    return list(
        FeaturedBlogPurchase.objects.filter(user=user).values_list("post_id", flat=True)
    )
