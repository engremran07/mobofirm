"""Offer pages: the list of running offers and one offer's landing page."""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.decorators import require_app_enabled

from . import gateways
from .audience import audience_label
from .grants import record_offer_view
from .models import Promotion
from .offers import card_for, offer_state, public_promotions

VIEW_DEDUPE_SECONDS = 30 * 60
SOURCES = frozenset({"banner", "notification"})


def can_preview(user: Any) -> bool:
    return bool(user.is_authenticated and user.has_perm("promotions.view_promotion"))


def visible_promotion(user: Any, slug: str) -> Promotion:
    """The promotion at ``slug`` if the visitor may see it, else a 404."""
    promotion = Promotion.objects.select_related("target_package").filter(slug=slug)
    found = promotion.first()
    if found is None:
        raise Http404("No such offer.")
    if can_preview(user):
        return found
    if not public_promotions().filter(pk=found.pk).exists():
        raise Http404("No such offer.")
    return found


def _viewer_key(request: HttpRequest) -> str:
    if request.user.is_authenticated:
        return f"u{request.user.pk}"
    return f"ip{request.META.get('REMOTE_ADDR', '')}"


def _count_view(request: HttpRequest, promotion: Promotion) -> None:
    """Count a page view once per visitor per half hour (bots refreshing do not inflate)."""
    if can_preview(request.user):
        return
    key = f"promotions:view:{promotion.pk}:{_viewer_key(request)}"
    if cache.add(key, 1, VIEW_DEDUPE_SECONDS):
        source = request.GET.get("src", "")
        record_offer_view(promotion, source if source in SOURCES else "")


@require_GET
@require_app_enabled("promotions")
def index(request: HttpRequest) -> HttpResponse:
    cards = [card_for(request.user, promotion) for promotion in public_promotions()]
    return render(
        request,
        "promotions/index.html",
        {
            "running": [card for card in cards if card.state != "ended"],
            "ended": [card for card in cards if card.state == "ended"],
        },
    )


@require_GET
@require_app_enabled("promotions")
def offer(request: HttpRequest, slug: str) -> HttpResponse:
    promotion = visible_promotion(request.user, slug)
    _count_view(request, promotion)
    state = offer_state(request.user, promotion)
    return render(
        request,
        "promotions/offer.html",
        {
            "promotion": promotion,
            "state": state,
            "effect": promotion.effect_summary(),
            "terms": [line for line in promotion.terms.splitlines() if line.strip()],
            "audience_text": audience_label(promotion),
            "is_preview": not promotion.is_live and can_preview(request.user),
            "earn_url": gateways.earn_url(),
        },
    )
