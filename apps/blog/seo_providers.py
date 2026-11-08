"""SEO page providers for the blog (registered by ``apps.seo``)."""

from __future__ import annotations

import calendar
from urllib.parse import urlsplit

from django.db.models import Q
from django.urls import reverse

from apps.firmwares.models import Brand, Chipset, Model
from apps.firmwares.seo_providers import model_label
from apps.seo import schema
from apps.seo.contentsources import (
    GraphData,
    GraphEdge,
    GraphNode,
    LinkableInfo,
    register_content_source,
    register_graph_source,
)
from apps.seo.contracts import Crumb, PageSEO
from apps.seo.models import SEOSettings
from apps.seo.policy import clean_text
from apps.seo.providers import ProviderContext, register_provider
from apps.seo.sitemap_registry import register_sitemap_filter
from apps.site_settings.services import get_site_settings

from .models import Category, Post
from .paywall import teaser_text
from .selectors import author_name

HOME = ("Home", "/")
PAYWALL_SELECTOR = ".article-paywall"


def _crumbs(*items: tuple[str, str]) -> list[Crumb]:
    return [Crumb(name, path) for name, path in items]


def _blog_crumbs(*extra: tuple[str, str]) -> list[Crumb]:
    return _crumbs(HOME, ("Blog", reverse("blog:post_list")), *extra)


def post_is_indexable(post: Post) -> bool:
    return post.is_live and not post.noindex


def _posts_filter(items: list, _row: SEOSettings) -> list:
    """Own-canonical, indexable posts only; a canonical elsewhere is not listed."""
    return [
        post
        for post in items
        if post_is_indexable(post)
        and (
            not post.canonical_url
            or urlsplit(post.canonical_url).path == post.get_absolute_url()
        )
    ]


def _blog_graph() -> GraphData:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for post in Post.objects.live().filter(noindex=False):
        key = f"post:{post.pk}"
        nodes.append(GraphNode(key, post.title, "post", post.get_absolute_url()))
        if post.firmware_brand_id:
            edges.append(
                GraphEdge(key, f"brand:{post.firmware_brand_id}", "covers_brand")
            )
        if post.firmware_model_id:
            edges.append(
                GraphEdge(key, f"model:{post.firmware_model_id}", "covers_model")
            )
    return GraphData(nodes, edges)


register_graph_source(_blog_graph)
register_sitemap_filter("blog", _posts_filter)
register_content_source(
    "blog.post",
    url=lambda post: post.get_absolute_url(),
    is_public=post_is_indexable,
    linkable=lambda post: LinkableInfo(
        post.title, post.get_absolute_url(), post.summary or ""
    ),
    notify_on_edit=True,
)


@register_provider("blog:post_list")
def post_list(ctx: ProviderContext) -> PageSEO:
    request = ctx.request
    path = reverse("blog:post_list")
    category_slug = request.GET.get("category", "").strip()
    tag = request.GET.get("tag", "").strip()
    if category_slug and not tag:
        category = Category.objects.filter(slug=category_slug).first()
        total = Post.objects.live().filter(category=category).count() if category else 0
        if category is not None:
            return PageSEO(
                page_type="blog_category",
                title=f"{category.name} posts",
                description=ctx.describe(
                    "", f"{total} published posts in {category.name}."
                ),
                path=path,
                canonical_params=("category",),
                crumbs=_blog_crumbs(
                    (category.name, f"{path}?category={category.slug}")
                ),
                object_ref=("blog.category", str(category.pk)),
                index=total >= max(ctx.settings.min_posts_for_tag_index, 1),
                noindex_reason="category has no published posts",
            )
    filtered = bool(category_slug or tag)
    return PageSEO(
        page_type="blog_list",
        title="Blog: firmware news and flashing guides",
        description=ctx.describe(
            "", "Firmware release news, device guides and flashing tutorials."
        ),
        path=path,
        crumbs=_blog_crumbs(),
        index=not filtered,
        noindex_reason="filtered blog listing",
    )


@register_provider("blog:post_detail")
def post_detail(ctx: ProviderContext) -> PageSEO | None:
    post = (
        Post.objects.select_related("author", "category")
        .filter(slug=ctx.kwargs.get("slug", ""))
        .first()
    )
    if post is None:
        return None
    path = post.get_absolute_url()
    published = post.published_at or post.publish_at
    description = ctx.describe(
        post.seo_description or post.summary,
        clean_text(teaser_text(post) if post.is_gated else post.body),
    )
    crumbs = _blog_crumbs()
    if post.category is not None:
        crumbs.append(
            Crumb(
                post.category.name,
                f"{reverse('blog:post_list')}?category={post.category.slug}",
            )
        )
    crumbs.append(Crumb(post.title, path))
    author = author_name(post)
    absolute = ctx.absolute(path)
    page = PageSEO(
        page_type="blog_post",
        title=post.seo_title or post.title,
        description=description,
        path=path,
        canonical=post.canonical_url,
        image=post.cover_image_url,
        og_type="article",
        crumbs=crumbs,
        object_ref=("blog.post", str(post.pk)),
        index=post_is_indexable(post),
        noindex_reason="post is not live or is marked noindex",
        published=published,
        modified=post.updated_at,
        sources={"canonical": "object" if post.canonical_url else "route"},
    )
    posting = schema.blog_posting(
        headline=post.title,
        url=post.canonical_url or absolute,
        description=description,
        image=ctx.absolute(post.cover_image_url),
        published=published,
        modified=post.updated_at,
        author_name=author,
        publisher_name=ctx.settings.site_name or get_site_settings().site_name,
    )
    if post.is_gated:
        # Google paywalled-content markup: the gated part is the paywall element.
        posting["isAccessibleForFree"] = False
        posting["hasPart"] = {
            "@type": "WebPageElement",
            "isAccessibleForFree": False,
            "cssSelector": PAYWALL_SELECTOR,
        }
    page.schema.append(posting)
    return page


@register_provider("blog:post_archive_year", "blog:post_archive_month")
def archive(ctx: ProviderContext) -> PageSEO | None:
    year = ctx.kwargs.get("year")
    month = ctx.kwargs.get("month")
    if not isinstance(year, int):
        return None
    posts = Post.objects.live().filter(publish_at__year=year)
    if isinstance(month, int):
        if not 1 <= month <= 12:
            return None
        posts = posts.filter(publish_at__month=month)
        label = f"{calendar.month_name[month]} {year}"
        path = reverse("blog:post_archive_month", kwargs={"year": year, "month": month})
    else:
        label = str(year)
        path = reverse("blog:post_archive_year", kwargs={"year": year})
    total = posts.count()
    return PageSEO(
        page_type="blog_archive",
        title=f"Blog archive: {label}",
        description=ctx.describe("", f"{total} posts published in {label}."),
        path=path,
        crumbs=_blog_crumbs((label, path)),
        index=total > 0,
        noindex_reason="no posts in this period",
    )


@register_provider("blog:brand_posts")
def brand_posts(ctx: ProviderContext) -> PageSEO | None:
    brand = Brand.objects.filter(slug=ctx.kwargs.get("brand_slug", "")).first()
    if brand is None:
        return None
    total = Post.objects.live().filter(firmware_brand=brand).count()
    path = reverse("blog:brand_posts", kwargs={"brand_slug": brand.slug})
    return PageSEO(
        page_type="blog_brand",
        title=f"{brand.name} firmware news and guides",
        description=ctx.describe("", f"{total} posts about {brand.name} firmware."),
        path=path,
        crumbs=_blog_crumbs((brand.name, path)),
        index=total > 0,
        noindex_reason="no published posts for this brand",
    )


@register_provider("blog:model_posts")
def model_posts(ctx: ProviderContext) -> PageSEO | None:
    model = (
        Model.objects.select_related("brand")
        .filter(
            brand__slug=ctx.kwargs.get("brand_slug", ""),
            slug=ctx.kwargs.get("model_slug", ""),
        )
        .first()
    )
    if model is None:
        return None
    total = Post.objects.live().filter(firmware_model=model).count()
    path = reverse(
        "blog:model_posts",
        kwargs={"brand_slug": model.brand.slug, "model_slug": model.slug},
    )
    label = model_label(model)
    return PageSEO(
        page_type="blog_model",
        title=f"{label} firmware news and guides",
        description=ctx.describe("", f"{total} posts about {label} firmware."),
        path=path,
        crumbs=_blog_crumbs((label, path)),
        index=total > 0,
        noindex_reason="no published posts for this model",
    )


@register_provider("blog:chipset_posts")
def chipset_posts(ctx: ProviderContext) -> PageSEO | None:
    chipset = Chipset.objects.filter(slug=ctx.kwargs.get("slug", "")).first()
    if chipset is None:
        return None
    total = (
        Post.objects.live()
        .filter(
            Q(firmware_model__chipset_ref=chipset)
            | Q(firmware_automation_link__chipset=chipset)
        )
        .distinct()
        .count()
    )
    path = reverse("blog:chipset_posts", kwargs={"slug": chipset.slug})
    label = chipset.display_name
    return PageSEO(
        page_type="blog_chipset",
        title=f"{label} firmware news and guides",
        description=ctx.describe("", f"{total} posts about {label} firmware."),
        path=path,
        crumbs=_blog_crumbs((label, path)),
        index=total > 0,
        noindex_reason="no published posts for this chipset",
    )


@register_provider("blog:blog_search")
def search(_ctx: ProviderContext) -> PageSEO:
    return PageSEO(
        page_type="search",
        title="Search the blog",
        index=False,
        noindex_reason="internal search results",
    )
