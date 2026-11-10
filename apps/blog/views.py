"""Public blog pages: journal, article, brand/model/chipset hubs and archives."""

from __future__ import annotations

import calendar
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.db.models.functions import ExtractMonth
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

from apps.blog import selectors, shop_gateway
from apps.blog.access import (
    blog_enabled_for,
    can_edit_post,
    can_manage_post,
    comments_enabled,
)
from apps.blog.firmware_panel import firmware_inventory, firmware_summary
from apps.blog.models import Category, Post, PostTranslation
from apps.blog.paywall import resolve_gate, teaser_text
from apps.blog.rendering import RenderedArticle, render_article
from apps.comments.selectors import comment_tree
from apps.core.app_service import AppService
from apps.core.search import list_search_context
from apps.core.view_counter import should_count
from apps.site_settings.services import absolute_url
from apps.tags.selectors import tag_cloud

logger = logging.getLogger(__name__)


def _require_blog(request: HttpRequest) -> None:
    if not blog_enabled_for(request.user):
        raise Http404("The blog is disabled.")


def _with_internal_links(html: SafeString, post: Post) -> SafeString:
    """Insert reviewed internal links when the SEO app is installed."""
    seo = AppService.get("seo")
    if seo is None:
        return html
    return mark_safe(seo.link_body_html(str(html), post))


def _apply_translations(posts: list[Post], language: str) -> None:
    """Swap in stored translations for ``language`` (in memory, display only)."""
    if not language or not posts:
        return
    translations = {
        translation.post_id: translation
        for translation in PostTranslation.objects.filter(
            post__in=posts, language=language
        )
    }
    for post in posts:
        translation = translations.get(post.pk)
        if translation is None:
            continue
        post.title = translation.title or post.title
        post.summary = translation.summary or post.summary
        post.body = translation.body or post.body
        post.seo_title = translation.seo_title or post.seo_title
        post.seo_description = translation.seo_description or post.seo_description


def _cards(posts, request: HttpRequest) -> list[selectors.PostCard]:
    posts = list(posts)
    _apply_translations(posts, getattr(request, "LANGUAGE_CODE", ""))
    return selectors.build_cards(posts)


def _listing_context(request: HttpRequest, base) -> dict:
    """Shared context for any paginated list of posts with the sidebar."""
    page_obj = Paginator(base, selectors.PAGE_SIZE).get_page(
        request.GET.get("page") or 1
    )
    return {
        "cards": _cards(page_obj.object_list, request),
        "page_obj": page_obj,
        "total": page_obj.paginator.count,
        "trending": selectors.build_cards(selectors.trending_posts(5)),
        "cloud": tag_cloud(24),
    }


@require_GET
@vary_on_headers("HX-Request")
def post_list(request: HttpRequest) -> HttpResponse:
    _require_blog(request)
    filters = selectors.ListingFilters.from_request(request.GET)
    posts, search_result = selectors.listing(filters)
    lead_post = None if filters.is_filtered else selectors.lead_story()
    if lead_post is not None:
        posts = posts.exclude(pk=lead_post.pk)
    on_first_page = (request.GET.get("page") or "1") == "1"
    lead = lead_post if on_first_page else None
    context = _listing_context(request, posts)
    context |= list_search_context(
        search_result,
        context["total"],
        user=request.user,
        kinds=("brand", "model", "chipset", "tag", "blog_category"),
    )
    active_category = None
    if filters.category:
        active_category = Category.objects.filter(slug=filters.category).first()
    context |= {
        "filters": filters,
        "lead": _cards([lead], request)[0] if lead is not None else None,
        "chips": selectors.category_chips(),
        "active_category": active_category,
        "sort_options": selectors.SORT_OPTIONS,
        "latest": selectors.build_cards(selectors.latest_posts(5)),
        "query_string": filters.query_string(),
        "chip_all_url": f"?{filters.query_string(category='')}",
    }
    if request.headers.get("HX-Request"):
        return render(request, "blog/partials/results.html", context)
    return render(request, "blog/post_list.html", context)


@require_GET
@vary_on_headers("HX-Request")
def blog_search(request: HttpRequest) -> HttpResponse:
    """Search is the journal with ``q`` applied, so filters and paging stay shared."""
    return post_list(request)


@login_required
@require_GET
def purchased(request: HttpRequest) -> HttpResponse:
    """Featured and paid articles the signed-in member bought (the shop lists them)."""
    _require_blog(request)
    ids = shop_gateway.purchased_post_ids(request.user)
    by_id = {post.pk: post for post in selectors.live_posts().filter(pk__in=ids)}
    posts = [by_id[pk] for pk in ids if pk in by_id]
    return render(
        request,
        "blog/purchased.html",
        {
            "cards": _cards(posts, request),
            "shop_lists_purchases": bool(ids) or shop_gateway.available(),
        },
    )


def _count_view(request: HttpRequest, post: Post) -> None:
    """Count a human view once per session per day (atomic ``F()`` update)."""
    if should_count(request, "post", post.pk):
        Post.objects.filter(pk=post.pk).update(views_count=F("views_count") + 1)
        post.views_count += 1


@require_GET
def post_detail(request: HttpRequest, slug: str) -> HttpResponse:
    _require_blog(request)
    post = get_object_or_404(
        Post.objects.select_related(
            "author",
            "category",
            "firmware_brand",
            "firmware_model",
            "firmware_automation_link",
            "guide_request",
        ).prefetch_related("tags"),
        slug__iexact=slug,
    )
    if not (post.is_live or can_manage_post(request.user, post)):
        raise Http404("This article is not published.")
    if post.is_live:
        _count_view(request, post)
    language = getattr(request, "LANGUAGE_CODE", "")
    _apply_translations([post], language)

    gate = resolve_gate(request.user, post)
    if gate.locked:
        article = RenderedArticle(
            html=format_html("<p>{}</p>", teaser_text(post)), toc=[], word_count=0
        )
    else:
        article = render_article(post.body)
        article.html = _with_internal_links(article.html, post)
    tags = [tag for tag in post.tags.all() if selectors.is_public_tag(tag)]
    card = selectors.build_card(post, tags)
    older, newer = selectors.adjacent_posts(post)
    comments_open = comments_enabled() and post.allow_comments and not gate.locked
    context = {
        "post": post,
        "gate": gate,
        "card": card,
        "article": article,
        "tags": tags,
        "cover": post.cover_image_url,
        "share_url": post.canonical_url
        or absolute_url(post.get_absolute_url(), request),
        "related": selectors.build_cards(selectors.related_posts(post, 3)),
        "older": selectors.build_card(older) if older else None,
        "newer": selectors.build_card(newer) if newer else None,
        "author_post_count": selectors.live_posts().filter(author=post.author).count(),
        "is_editorial": selectors.is_editorial(post),
        "author_initials": selectors.initials_for(card.author),
        "firmware": firmware_summary(post),
        "show_inventory": bool(post.firmware_model_id)
        and not hasattr(post, "firmware_automation_link"),
        "inventory": firmware_inventory(post)
        if post.firmware_model_id and not hasattr(post, "firmware_automation_link")
        else [],
        "latest": selectors.build_cards(selectors.latest_posts(4, exclude_pk=post.pk)),
        "cloud": tag_cloud(16),
        "can_edit": can_edit_post(request.user, post),
        "comments_open": comments_open,
        "comment_nodes": comment_tree(post, request.user, timezone.now())
        if comments_open
        else [],
        "comment_count": post.comments_count,
        "is_preview": not post.is_live,
    }
    response = render(request, "blog/post_detail.html", context)
    if post.is_gated:
        # The same URL shows different content per reader: never share it.
        patch_cache_control(response, private=True)
    return response


def _hub(
    request: HttpRequest,
    *,
    template: str,
    posts,
    lead_post: Post | None,
    extra: dict,
) -> HttpResponse:
    """Brand, model and chipset hubs: the entity's own post leads, the rest is paged."""
    _require_blog(request)
    if lead_post is not None and lead_post.is_live:
        posts = posts.exclude(pk=lead_post.pk)
    else:
        lead_post = None
    context = _listing_context(request, posts.order_by("-published_at", "-pk"))
    context["lead"] = selectors.build_card(lead_post) if lead_post else None
    context.update(extra)
    return render(request, template, context)


def _entity_post(kind: str, **lookup) -> Post | None:
    from apps.firmwares.models import AutomatedBlogLink

    link = (
        AutomatedBlogLink.objects.select_related(
            "post__author", "post__category", "post__firmware_brand"
        )
        .prefetch_related("post__tags")
        .filter(kind=kind, **lookup)
        .first()
    )
    return link.post if link else None


@require_GET
def brand_posts(request: HttpRequest, brand_slug: str) -> HttpResponse:
    from apps.firmwares.models import Brand

    brand = get_object_or_404(Brand, slug=brand_slug)
    posts = selectors.live_posts().filter(firmware_brand=brand)
    other_brands = (
        Brand.objects.filter(blog_posts__status="published")
        .exclude(pk=brand.pk)
        .annotate(post_total=Count("blog_posts", distinct=True))
        .order_by("-post_total", "name")[:12]
    )
    return _hub(
        request,
        template="blog/brand_posts.html",
        posts=posts,
        lead_post=_entity_post("brand", brand=brand),
        extra={"brand": brand, "other_brands": other_brands},
    )


@require_GET
def model_posts(request: HttpRequest, brand_slug: str, model_slug: str) -> HttpResponse:
    from apps.firmwares.models import Brand
    from apps.firmwares.models import Model as DeviceModel

    brand = get_object_or_404(Brand, slug=brand_slug)
    model = get_object_or_404(
        DeviceModel.objects.select_related("chipset_ref"), brand=brand, slug=model_slug
    )
    posts = selectors.live_posts().filter(firmware_model=model)
    return _hub(
        request,
        template="blog/model_posts.html",
        posts=posts,
        lead_post=_entity_post("model", model=model),
        extra={"brand": brand, "model": model, "chipset": model.chipset_ref},
    )


@require_GET
def chipset_posts(request: HttpRequest, slug: str) -> HttpResponse:
    from apps.firmwares.models import Chipset

    chipset = get_object_or_404(Chipset, slug=slug)
    posts = selectors.live_posts().filter(
        Q(firmware_model__chipset_ref=chipset)
        | Q(firmware_automation_link__chipset=chipset)
    )
    return _hub(
        request,
        template="blog/chipset_posts.html",
        posts=posts.distinct(),
        lead_post=_entity_post("chipset", chipset=chipset),
        extra={"chipset": chipset},
    )


def _archive_posts(year: int, month: int | None = None):
    posts = selectors.live_posts().filter(publish_at__year=year)
    if month is not None:
        posts = posts.filter(publish_at__month=month)
    return posts.order_by("-published_at", "-pk")


def _archive_months(year: int) -> list[dict]:
    rows = (
        selectors.live_posts()
        .filter(publish_at__year=year)
        .annotate(month=ExtractMonth("publish_at"))
        .values("month")
        .annotate(total=Count("pk"))
        .order_by("month")
    )
    return [
        {
            "month": row["month"],
            "name": calendar.month_name[row["month"]],
            "total": row["total"],
        }
        for row in rows
    ]


@require_GET
def post_archive_year(request: HttpRequest, year: int) -> HttpResponse:
    _require_blog(request)
    if not 1990 <= year <= 2100:
        raise Http404
    context = _listing_context(request, _archive_posts(year))
    context |= {"year": year, "months": _archive_months(year), "month": None}
    return render(request, "blog/archive.html", context)


@require_GET
def post_archive_month(request: HttpRequest, year: int, month: int) -> HttpResponse:
    _require_blog(request)
    if not (1990 <= year <= 2100 and 1 <= month <= 12):
        raise Http404
    context = _listing_context(request, _archive_posts(year, month))
    context |= {
        "year": year,
        "month": month,
        "month_name": calendar.month_name[month],
        "months": _archive_months(year),
    }
    return render(request, "blog/archive.html", context)
