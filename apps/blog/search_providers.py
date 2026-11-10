"""Search providers for blog posts and categories (live content only)."""

from __future__ import annotations

from collections.abc import Mapping

from django.db.models import Count, Q, QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.text import Truncator

from apps.core.search import QuerySetProvider, SearchContext, SearchHit, register

from .access import blog_enabled_for
from .models import Category, Post


class BlogPostProvider(QuerySetProvider[Post]):
    """Live articles; the popular list ranks by total reads (no windowed counter)."""

    kind = "blog"
    label = "Article"
    label_plural = "Articles"
    popular_title = "Popular articles"
    icon = "newspaper"
    priority = 70
    search_fields = ("title", "summary", "category__name", "tags__name")
    ordering = ("-published_at", "-pk")

    def popular(
        self, *, limit: int, window_days: int, scope: Mapping[str, str]
    ) -> list[SearchHit]:
        posts = Post.objects.live().select_related("category")
        if scope.get("brand"):
            posts = posts.filter(firmware_brand__slug=scope["brand"])
        if scope.get("chipset"):
            posts = posts.filter(
                Q(firmware_model__chipset_ref__slug=scope["chipset"])
                | Q(firmware_automation_link__chipset__slug=scope["chipset"])
            )
        context = SearchContext(query="", tokens=(), limit=limit)
        rows = posts.order_by("-views_count", "-published_at")[:limit]
        return [self.build_hit(post, 0.0, context) for post in rows]

    def is_visible_to(self, user: object | None) -> bool:
        return blog_enabled_for(user)

    def get_queryset(self, context: SearchContext) -> QuerySet[Post]:
        return Post.objects.live().select_related("category").distinct()

    def score_fields(self, obj: Post) -> tuple[str, ...]:
        return (obj.title, obj.category.name if obj.category else "", obj.summary)

    def build_hit(self, obj: Post, score: float, context: SearchContext) -> SearchHit:
        category = obj.category.name if obj.category else ""
        published = obj.published_at.strftime("%b %d, %Y") if obj.published_at else ""
        return SearchHit(
            kind=self.kind,
            title=obj.title,
            subtitle=" · ".join(
                part
                for part in (category, published, Truncator(obj.summary).chars(80))
                if part
            ),
            url=reverse("blog:post_detail", args=[obj.slug]),
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.title,
        )


class BlogCategoryProvider(QuerySetProvider[Category]):
    """Article categories that have at least one live post."""

    kind = "blog_category"
    label = "Article category"
    label_plural = "Article categories"
    icon = "tags"
    priority = 75
    search_fields = ("name", "slug")
    ordering = ("name",)
    suggestible = True
    watches = (Category,)

    def is_visible_to(self, user: object | None) -> bool:
        return blog_enabled_for(user)

    def get_queryset(self, context: SearchContext) -> QuerySet[Category]:
        live = Q(posts__status="published", posts__publish_at__lte=timezone.now())
        return Category.objects.annotate(post_count=Count("posts", filter=live)).filter(
            post_count__gt=0
        )

    def score_fields(self, obj: Category) -> tuple[str, ...]:
        return (obj.name, obj.slug)

    def build_hit(
        self, obj: Category, score: float, context: SearchContext
    ) -> SearchHit:
        total = int(getattr(obj, "post_count", 0))
        return SearchHit(
            kind=self.kind,
            title=obj.name,
            subtitle=f"{total} article{'s' if total != 1 else ''}",
            url=f"{reverse('blog:post_list')}?category={obj.slug}",
            icon=self.icon,
            score=score,
            value=str(obj.pk),
            label=obj.name,
        )


register(BlogPostProvider())
register(BlogCategoryProvider())
