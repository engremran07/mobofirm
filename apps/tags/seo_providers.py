"""SEO page providers for tags (registered by ``apps.seo``)."""

from __future__ import annotations

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.seo.contentsources import register_content_source
from apps.seo.contracts import Crumb, PageSEO
from apps.seo.models import SEOSettings
from apps.seo.policy import clean_text
from apps.seo.providers import ProviderContext, register_provider
from apps.seo.sitemap_registry import register_sitemap_filter

from .models import Tag

HOME = ("Home", "/")


def live_post_filter() -> Q:
    """Posts readers can see through the ``posts`` relation, excluding noindex."""
    return Q(posts__status="published", posts__publish_at__lte=timezone.now()) & Q(
        posts__noindex=False
    )


def tags_with_posts(minimum: int) -> set[int]:
    rows = (
        Tag.objects.filter(is_active=True, is_deleted=False)
        .annotate(live_posts=Count("posts", filter=live_post_filter(), distinct=True))
        .filter(live_posts__gte=max(minimum, 1))
        .values_list("pk", flat=True)
    )
    return set(rows)


@register_provider("tags:list")
def tag_list(ctx: ProviderContext) -> PageSEO:
    path = reverse("tags:list")
    return PageSEO(
        page_type="tag_list",
        title="Topics and tags",
        description=ctx.describe("", "Browse firmware and device topics by tag."),
        path=path,
        crumbs=[Crumb(*HOME), Crumb("Tags", path)],
    )


@register_provider("tags:detail")
def tag_detail(ctx: ProviderContext) -> PageSEO | None:
    tag = Tag.objects.filter(slug=ctx.kwargs.get("slug", ""), is_deleted=False).first()
    if tag is None:
        return None
    total = (
        Tag.objects.filter(pk=tag.pk)
        .annotate(live_posts=Count("posts", filter=live_post_filter(), distinct=True))
        .values_list("live_posts", flat=True)
        .first()
        or 0
    )
    path = reverse("tags:detail", kwargs={"slug": tag.slug})
    minimum = max(ctx.settings.min_posts_for_tag_index, 1)
    return PageSEO(
        page_type="tag",
        title=f"{tag.name} posts and guides",
        description=ctx.describe(
            clean_text(tag.description), f"{total} posts tagged {tag.name}."
        ),
        path=path,
        crumbs=[
            Crumb(*HOME),
            Crumb("Tags", reverse("tags:list")),
            Crumb(tag.name, path),
        ],
        object_ref=("tags.tag", str(tag.pk)),
        index=tag.is_active and total >= minimum,
        noindex_reason=f"tag has fewer than {minimum} published posts or is inactive",
        modified=tag.updated_at,
    )


@register_provider("tags:search")
def search(_ctx: ProviderContext) -> PageSEO:
    return PageSEO(
        page_type="search",
        title="Search tags",
        index=False,
        noindex_reason="internal search results",
    )


def _tags_filter(items: list, row: SEOSettings) -> list:
    allowed = tags_with_posts(row.min_posts_for_tag_index)
    return [tag for tag in items if tag.pk in allowed]


register_sitemap_filter("tags", _tags_filter)
register_content_source(
    "tags.tag",
    url=lambda tag: reverse("tags:detail", kwargs={"slug": tag.slug}),
    is_public=lambda tag: tag.is_active and not tag.is_deleted,
)
