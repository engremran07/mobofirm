"""Search provider for approved marketplace listings of verified sellers."""

from __future__ import annotations

from django.db.models import QuerySet
from django.utils.text import Truncator

from apps.core.search import QuerySetProvider, SearchContext, SearchHit, register

from .models import Listing
from .services import public_listings

LISTING_FIELDS = ("title", "description", "seller__display_name")


class ListingProvider(QuerySetProvider[Listing]):
    """Approved listings of active, verified sellers (never pending or rejected)."""

    kind = "listing"
    label = "Marketplace listing"
    label_plural = "Marketplace listings"
    noun = "listing"
    icon = "store"
    priority = 85
    search_fields = LISTING_FIELDS
    ordering = ("-is_featured", "-created_at")

    def get_queryset(self, context: SearchContext) -> QuerySet[Listing]:
        return public_listings()

    def score_fields(self, obj: Listing) -> tuple[str, ...]:
        return (obj.title, obj.seller.display_name, obj.description)

    def build_hit(
        self, obj: Listing, score: float, context: SearchContext
    ) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.title,
            subtitle=Truncator(obj.description).chars(90) or obj.seller.display_name,
            url=obj.get_absolute_url(),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.title,
        )


register(ListingProvider())
