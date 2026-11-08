"""Search provider for CMS pages the requesting user may read."""

from __future__ import annotations

from django.db import DatabaseError
from django.db.models import QuerySet
from django.utils.text import Truncator

from apps.core.app_service import app_enabled
from apps.core.search import QuerySetProvider, SearchContext, SearchHit, register

from .models import Page


class PageProvider(QuerySetProvider[Page]):
    kind = "page"
    label = "Page"
    label_plural = "Pages"
    icon = "file-text"
    priority = 90
    search_fields = ("title", "slug", "seo_title", "seo_description")
    ordering = ("title",)

    def is_visible_to(self, user: object | None) -> bool:
        try:
            return app_enabled("pages")
        except DatabaseError:
            return False

    def get_queryset(self, context: SearchContext) -> QuerySet[Page]:
        return Page.objects.visible_to(context.user)

    def score_fields(self, obj: Page) -> tuple[str, ...]:
        return (obj.title, obj.slug, obj.seo_title, obj.seo_description)

    def build_hit(self, obj: Page, score: float, context: SearchContext) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=obj.title,
            subtitle=Truncator(obj.seo_description).chars(90),
            url=obj.get_absolute_url(),
            icon=self.icon,
            score=score,
            value=obj.slug,
            label=obj.title,
        )


register(PageProvider())
