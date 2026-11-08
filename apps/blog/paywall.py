"""Featured and paid posts: who may read the body, and what everyone else sees.

The body of a gated post never leaves the server for a reader without access: pages
render :func:`teaser_text` instead, and feeds, search snippets, meta descriptions and
cards all go through :func:`public_excerpt`. Access for non-staff goes through the
shop contract (``apps.blog.shop_gateway``); when the shop is absent or denies, the
teaser is shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from html import unescape

from django.apps import apps
from django.urls import NoReverseMatch, reverse
from django.utils.html import strip_tags

from apps.blog import credits as credit_rules
from apps.blog import shop_gateway
from apps.blog.access import blog_settings, can_manage_post
from apps.blog.models import Post
from apps.blog.rendering import plain_excerpt
from apps.core.app_service import app_enabled

TEASER_SHARE = 0.4
MIN_TEASER_WORDS = 10
_WORDS = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class PostGate:
    """What the current reader may see of one post."""

    gated: bool
    locked: bool
    via: str = "free"
    reason: str = ""
    credit_price: int | None = None
    price_usd: Decimal | None = None
    can_buy: bool = False
    needs_login: bool = False
    packages_url: str = ""


OPEN = PostGate(gated=False, locked=False)


def packages_url() -> str:
    """The shop's pricing page when the shop is installed and switched on."""
    if not apps.is_installed("apps.shop") or not app_enabled("shop"):
        return ""
    try:
        return reverse("shop:pricing")
    except NoReverseMatch:
        return ""


def resolve_gate(user: object, post: Post) -> PostGate:
    """Decide whether ``user`` reads the full body of ``post`` or sees the teaser."""
    if not post.is_gated:
        return OPEN
    if can_manage_post(user, post):
        return PostGate(gated=True, locked=False, via="staff")
    price = post.price
    credit_price = credit_rules.credits_for(price)
    if not getattr(user, "is_authenticated", False):
        return PostGate(
            gated=True,
            locked=True,
            via="anonymous",
            credit_price=credit_price,
            price_usd=price,
            needs_login=True,
            packages_url=packages_url(),
        )
    access = shop_gateway.featured_blog_access(user, post.pk, price)
    if access.allowed:
        return PostGate(
            gated=True,
            locked=False,
            via=access.via or "package",
            price_usd=price,
            credit_price=access.credit_price or credit_price,
        )
    credit_price = access.credit_price if access.credit_price else credit_price
    can_buy = (
        price is not None
        and credit_price is not None
        and credit_price > 0
        and shop_gateway.available()
    )
    return PostGate(
        gated=True,
        locked=True,
        via=access.via or "denied",
        reason=access.reason,
        credit_price=credit_price,
        price_usd=price,
        can_buy=can_buy,
        packages_url=packages_url(),
    )


def _plain(html: str) -> str:
    return " ".join(unescape(strip_tags(html or "")).split())


def teaser_text(post: Post) -> str:
    """The first words of the body: a share of a short article, never all of it."""
    words = _WORDS.findall(_plain(post.body))
    if not words:
        return ""
    limit = max(
        MIN_TEASER_WORDS,
        min(blog_settings().teaser_words, int(len(words) * TEASER_SHARE)),
    )
    if len(words) <= limit:
        limit = max(1, len(words) // 2)
    return " ".join(words[:limit]).rstrip(",.;: ") + "…"


def public_excerpt(post: Post, limit: int = 170) -> str:
    """Text safe for cards, feeds and snippets: never more than the teaser."""
    if post.summary:
        return plain_excerpt(post.summary, limit)
    source = teaser_text(post) if post.is_gated else post.body
    return plain_excerpt(source, limit)
