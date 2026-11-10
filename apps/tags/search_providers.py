"""Search provider for public tags that label at least one live post."""

from __future__ import annotations

from django.db.models import QuerySet
from django.urls import reverse

from apps.core.search import QuerySetProvider, SearchContext, SearchHit, register

from .models import Tag
from .selectors import public_tags, with_live_post_counts

TAG_FIELDS = ("name", "normalized_name", "synonyms_text")


class TagProvider(QuerySetProvider[Tag]):
    """Public tags that label at least one live post."""

    kind = "tag"
    label = "Tag"
    label_plural = "Tags"
    icon = "hash"
    priority = 80
    search_fields = TAG_FIELDS
    ordering = ("-usage_count", "name")
    suggestible = True
    watches = (Tag,)

    def get_queryset(self, context: SearchContext) -> QuerySet[Tag]:
        return with_live_post_counts(public_tags()).filter(live_count__gt=0)

    def score_fields(self, obj: Tag) -> tuple[str, ...]:
        return (obj.name, obj.slug, obj.synonyms_text)

    def build_hit(self, obj: Tag, score: float, context: SearchContext) -> SearchHit:
        total = int(getattr(obj, "live_count", 0))
        return SearchHit(
            kind=self.kind,
            title=obj.name,
            subtitle=f"{total} article{'s' if total != 1 else ''}",
            url=reverse("tags:detail", args=[obj.slug]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.name,
        )


register(TagProvider())
