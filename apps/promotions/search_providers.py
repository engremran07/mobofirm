"""Search provider: running public offers."""

from __future__ import annotations

from django.db import DatabaseError
from django.db.models import QuerySet
from django.utils.text import Truncator

from apps.core.app_service import app_enabled
from apps.core.search import QuerySetProvider, SearchContext, SearchHit, register

from .models import Promotion
from .offers import public_promotions


class OfferProvider(QuerySetProvider[Promotion]):
    kind = "offer"
    label = "Offer"
    label_plural = "Offers"
    icon = "megaphone"
    priority = 140
    search_fields = ("name", "slug", "headline", "description")
    ordering = ("name",)

    def is_visible_to(self, user: object | None) -> bool:
        try:
            return app_enabled("promotions")
        except DatabaseError:
            return False

    def get_queryset(self, context: SearchContext) -> QuerySet[Promotion]:
        return public_promotions()

    def score_fields(self, obj: Promotion) -> tuple[str, ...]:
        return (obj.name, obj.slug, obj.headline)

    def build_hit(
        self, obj: Promotion, score: float, context: SearchContext
    ) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.name,
            subtitle=Truncator(obj.effect_summary()).chars(90),
            url=obj.get_absolute_url(),
            icon=self.icon,
            score=score,
            value=obj.slug,
            label=obj.name,
        )


register(OfferProvider())
