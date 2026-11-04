from __future__ import annotations

import datetime
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Count, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.blog.models import CategoryTranslation, PostTranslation, TagTranslation
from apps.blog.services import ai_editor, workflow
from apps.blog.services.post_service import search_posts
from apps.core import ai_client
from apps.core.app_service import AppService
from apps.core.utils.logging import log_event
from apps.core.views import _get_site_settings_snapshot
from apps.tags import services as tag_services
from apps.tags.models import Tag
from apps.users.models import CustomUser
from apps.governance.services import GovernanceError

from .forms import CategoryForm, PostForm, SearchForm
from .models import Category, Post, PostDraft, PostRevision, PostStatus
from .services.history import PostHistoryService

logger = logging.getLogger(__name__)


def resolve_locale(request, _context: str = "") -> str:
    """Stub for removed i18n app. Returns the default language code."""
    return getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE)


def _sync_tag_usage(tags_qs: QuerySet) -> None:
    for tag in tags_qs:
        try:
            count = tag.posts.filter(
                status=PostStatus.PUBLISHED, publish_at__lte=timezone.now()
            ).count()
            if tag.usage_count != count:
                tag.usage_count = count
                tag.save(update_fields=["usage_count"])
        except Exception:  # noqa: S112
            continue


def _apply_translations_to_posts(
    posts: list | QuerySet, locale: str | None
) -> list | QuerySet:
    """
    Apply translations to a list/queryset of posts for the given locale.
    Mutates objects in place for display purposes.

    NOTE: Ensure posts queryset has .prefetch_related('tags') for optimal performance.
    """
    if not locale:
        return posts
    post_ids = [p.id for p in posts if getattr(p, "id", None)]
    if not post_ids:
        return posts
    translations = {
        pt.post_id: pt  # type: ignore[attr-defined]
        for pt in PostTranslation.objects.filter(post_id__in=post_ids, language=locale)
    }
    categories = {p.category_id for p in posts if getattr(p, "category_id", None)}  # type: ignore[attr-defined]
    cat_translations = (
        {
            ct.category_id: ct  # type: ignore[attr-defined]
            for ct in CategoryTranslation.objects.filter(
                category_id__in=categories, language=locale
            )
        }
        if categories
        else {}
    )

    # Collect tag IDs efficiently (assumes tags are prefetched)
    tag_ids = set()
    post_tags_map = {}  # Cache tags per post to avoid second iteration
    for p in posts:
        try:
            # Use list() to cache prefetched tags
            post_tags = [
                tag
                for tag in p.tags.all()
                if tag.is_curated and tag.is_active and not tag.is_deleted
            ]
            p.curated_tags = post_tags  # type: ignore[attr-defined]
            post_tags_map[p.id] = post_tags
            for t in post_tags:
                tag_ids.add(t.id)
        except Exception:
            post_tags_map[p.id] = []
            continue

    tag_translations = (
        {
            tt.tag_id: tt  # type: ignore[attr-defined]
            for tt in TagTranslation.objects.filter(tag_id__in=tag_ids, language=locale)
        }
        if tag_ids
        else {}
    )

    for p in posts:
        pt = translations.get(p.id)
        if pt:
            p.title = pt.title or p.title
            p.summary = pt.summary or p.summary
            p.body = pt.body or p.body
            p.seo_title = pt.seo_title or p.seo_title
            p.seo_description = pt.seo_description or p.seo_description
        if p.category_id and p.category_id in cat_translations:
            try:
                p.category.name = (
                    cat_translations[p.category_id].name or p.category.name
                )
            except Exception:  # noqa: S110
                pass
        # Use cached tags instead of hitting p.tags.all() again
        for t in post_tags_map.get(p.id, []):
            tt = tag_translations.get(t.id)
            if tt:
                t.name = tt.name or t.name
    return posts


@require_GET
def post_list(request: HttpRequest) -> HttpResponse:
    settings_snapshot = _get_site_settings_snapshot()
    try:
        blog_api = AppService.get("blog")
        blog_settings = (
            blog_api.get_settings()
            if blog_api and hasattr(blog_api, "get_settings")
            else {}
        )
    except Exception:
        blog_api = None
        blog_settings = {}

    blog_enabled = blog_settings.get(
        "enable_blog",
        False if blog_api is None else settings_snapshot.get("enable_blog", True),
    )
    if not blog_enabled and not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
    ):
        raise Http404("Blog is disabled.")
    allow_user_posts = blog_settings.get(
        "allow_user_blog_posts", settings_snapshot.get("allow_user_blog_posts", False)
    )

    now_ts = timezone.now()
    base_qs = Post.objects.select_related("author", "category").prefetch_related("tags")
    published_qs = base_qs.filter(status=PostStatus.PUBLISHED, publish_at__lte=now_ts)

    # If owner/staff, also show their drafts/scheduled in manager context (not to the public)
    show_unpublished = request.user.is_authenticated and (
        getattr(request.user, "is_staff", False) or allow_user_posts
    )
    if show_unpublished:
        own_unpublished = base_qs.filter(
            author=request.user, status__in=[PostStatus.DRAFT, PostStatus.SCHEDULED]
        )
        posts = (published_qs | own_unpublished).distinct()
    else:
        posts = published_qs
    q = request.GET.get("q", "").strip()
    tag = request.GET.get("tag", "").strip()
    category_slug = request.GET.get("category", "").strip()
    author = request.GET.get("author", "").strip()
    status_filter = (request.GET.get("status") or "").strip().lower()
    sort = (request.GET.get("sort") or "recent").strip().lower()
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""

    if q:
        vector = (
            SearchVector("title", weight="A")
            + SearchVector("summary", weight="B")
            + SearchVector("body", weight="C")
            + SearchVector("tags__name", weight="B")
            + SearchVector("category__name", weight="C")
        )
        query = SearchQuery(q, search_type="plain")
        posts = (
            posts.annotate(rank=SearchRank(vector, query))
            .filter(rank__gte=0.05)
            .order_by("-rank", "-published_at")
        )
    if tag:
        posts = posts.filter(Q(tags__slug=tag) | Q(tags__name__iexact=tag))
    if category_slug:
        posts = posts.filter(
            Q(category__slug=category_slug) | Q(category__parent__slug=category_slug)
        )
    if author:
        posts = posts.filter(author__username=author)
    if date_from:
        try:
            df = datetime.datetime.fromisoformat(date_from)
            posts = posts.filter(publish_at__gte=df)
        except Exception:  # noqa: S110
            pass
    if date_to:
        try:
            dt = datetime.datetime.fromisoformat(date_to)
            posts = posts.filter(publish_at__lte=dt)
        except Exception:  # noqa: S110
            pass

    # Sorting
    if sort == "featured":
        posts = posts.order_by("-featured", "-published_at")
    elif sort == "oldest":
        posts = posts.order_by("published_at")
    elif sort == "title":
        posts = posts.order_by("title")
    else:
        sort = "recent"
        posts = posts.order_by("-published_at")

    posts = posts.prefetch_related("tags").distinct()

    # Status filter (only staff/author can view non-published states)
    if status_filter in PostStatus.values:
        if status_filter == PostStatus.PUBLISHED:
            posts = posts.filter(status=PostStatus.PUBLISHED, publish_at__lte=now_ts)
        elif getattr(request.user, "is_staff", False) or getattr(
            request.user, "is_superuser", False
        ):
            posts = posts.filter(status=status_filter)
        elif request.user.is_authenticated:
            posts = posts.filter(status=status_filter, author=request.user)
        # else: ignore invalid visibility requests
    paginator = Paginator(posts, 10)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    current_locale = resolve_locale(request, "blog")
    _apply_translations_to_posts(page_obj.object_list, current_locale)  # type: ignore[arg-type]
    # Precompute display strings to keep templates simple and avoid filter gymnastics.
    for p in page_obj:
        published = p.published_at.strftime("%b %d, %Y") if p.published_at else "Draft"
        p.meta_text = f"By {p.author} · {published}"  # type: ignore[attr-defined]
        p.card_image_url = ""  # type: ignore[attr-defined]

    trending_tags = Tag.objects.filter(
        is_active=True, is_curated=True, is_deleted=False
    ).order_by("-usage_count")[:10]
    for t in trending_tags:
        t.live_post_count = t.posts.filter(  # type: ignore[attr-defined]
            status=PostStatus.PUBLISHED, publish_at__lte=now_ts
        ).count()

    trending_posts = list(
        Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=now_ts)
        .select_related("author")
        .order_by("-featured", "-published_at")[:5]
    )
    if not trending_posts:
        trending_posts = list(
            Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=now_ts)
            .select_related("author")
            .order_by("-published_at")[:5]
        )
    latest_posts = Post.objects.filter(
        status=PostStatus.PUBLISHED, publish_at__lte=now_ts
    ).order_by("-published_at")[:5]
    # Get featured post (most recent featured or latest published)
    featured_post = (
        Post.objects.filter(
            status=PostStatus.PUBLISHED, publish_at__lte=now_ts, featured=True
        )
        .order_by("-published_at")
        .first()
    )

    if not featured_post:
        featured_post = (
            Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=now_ts)
            .order_by("-published_at")
            .first()
        )

    # Annotate categories with post count
    categories_with_count = (
        Category.objects.annotate(
            post_count=Count(
                "posts",
                filter=models.Q(
                    posts__status=PostStatus.PUBLISHED, posts__publish_at__lte=now_ts
                ),
            )
        )
        .select_related("parent")
        .order_by("parent__name", "name")
    )

    # Get popular tags for sidebar
    popular_tags = Tag.objects.filter(
        is_active=True, is_curated=True, is_deleted=False
    ).order_by("-usage_count")[:15]

    context = {
        "posts": page_obj,  # Pass page_obj directly for pagination
        "featured_post": featured_post,
        "page_obj": page_obj,
        "q": q,
        "trending_tags": trending_tags,
        "trending_posts": trending_posts,
        "popular_tags": popular_tags,
        "latest_posts": latest_posts,
        "allow_user_posts": allow_user_posts,
        "active_status": status_filter,
        "active_tag": tag,
        "active_category": category_slug,
        "active_author": author,
        "date_from": date_from,
        "date_to": date_to,
        "categories": categories_with_count,
        "selected_category": Category.objects.filter(slug=category_slug).first()
        if category_slug
        else None,
        "status_filters": [
            ("", "All"),
            (PostStatus.PUBLISHED, "Published"),
            (PostStatus.SCHEDULED, "Scheduled"),
            (PostStatus.DRAFT, "Drafts"),
        ],
        "sort": sort,
        "sort_options": [
            ("recent", "Newest"),
            ("featured", "Featured first"),
            ("oldest", "Oldest"),
            ("title", "Title A-Z"),
        ],
    }
    if request.headers.get("HX-Request"):
        return render(request, "blog/partials/post_list_items.html", context)
    return render(request, "blog/post_list.html", context)


@require_GET
def blog_search(request: HttpRequest) -> HttpResponse:
    """Blog search — returns full page or HTMX fragment for inline results."""
    form = SearchForm(request.GET or None)
    results: list = []
    query = ""
    if form.is_valid():
        query = form.cleaned_data["q"]
        results = list(search_posts(query))

    template = "blog/search.html"
    if request.headers.get("HX-Request"):
        template = "blog/fragments/search_results.html"

    return render(request, template, {"form": form, "results": results, "query": query})


@require_GET
def post_detail(request: HttpRequest, slug: str) -> HttpResponse:
    settings_snapshot = _get_site_settings_snapshot()
    try:
        blog_api = AppService.get("blog")
        blog_settings = (
            blog_api.get_settings()
            if blog_api and hasattr(blog_api, "get_settings")
            else {}
        )
    except Exception:
        blog_api = None
        blog_settings = {}
    blog_enabled = blog_settings.get(
        "enable_blog",
        False if blog_api is None else settings_snapshot.get("enable_blog", True),
    )
    if not blog_enabled and not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
    ):
        raise Http404("Blog is disabled.")
    allow_user_posts = blog_settings.get(
        "allow_user_blog_posts", settings_snapshot.get("allow_user_blog_posts", False)
    )

    base_qs = Post.objects.select_related("author", "category").prefetch_related(
        "tags",
        "tags__translations",  # Fix N+1: prefetch for tag translation lookups
    )
    post = get_object_or_404(base_qs, slug__iexact=slug)
    # Non-staff users must only see live posts
    if not (getattr(request.user, "is_staff", False) or request.user == post.author):
        if not post.is_live:
            raise Http404("Post not published.")
    # Fix N+1: Use tag PKs instead of evaluating queryset
    post.curated_tags = list(  # type: ignore[attr-defined]
        post.tags.filter(is_curated=True, is_active=True, is_deleted=False)
    )
    tag_ids = [tag.pk for tag in post.curated_tags]  # type: ignore[attr-defined]
    related = (
        Post.objects.filter(
            tags__id__in=tag_ids,
            status=PostStatus.PUBLISHED,
            publish_at__lte=timezone.now(),
        )
        .exclude(pk=post.pk)
        .distinct()
        .order_by("-published_at")[:4]
    )
    related_widget_html = render_to_string(
        "blog/partials/related_widget.html", {"posts": related}
    )
    trending_tags = Tag.objects.filter(
        is_active=True, is_curated=True, is_deleted=False
    ).order_by("-usage_count")[:10]
    trending_posts = list(
        Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=timezone.now())
        .select_related("author")
        .prefetch_related("tags")
        .order_by("-featured", "-published_at")[:5]
    )
    if not trending_posts:
        trending_posts = list(
            Post.objects.filter(
                status=PostStatus.PUBLISHED, publish_at__lte=timezone.now()
            )
            .select_related("author")
            .prefetch_related("tags")
            .order_by("-published_at")[:5]
        )
    # Tag cloud weights for sidebar (simple 3-tier scaling)
    tag_cloud_raw = list(
        Tag.objects.filter(is_active=True, is_curated=True, is_deleted=False).order_by(
            "-usage_count"
        )[:30]
    )
    if tag_cloud_raw:
        max_usage = max([t.usage_count or 1 for t in tag_cloud_raw]) or 1
        for t in tag_cloud_raw:
            ratio = (t.usage_count or 0) / max_usage
            if ratio >= 0.66:
                t.weight = "lg"  # type: ignore[attr-defined]
            elif ratio >= 0.33:
                t.weight = "md"  # type: ignore[attr-defined]
            else:
                t.weight = "sm"  # type: ignore[attr-defined]
    else:
        tag_cloud_raw = []

    canonical = post.canonical_url or request.build_absolute_uri()
    current_locale = resolve_locale(request, "blog")

    # Apply translations when available
    if current_locale:
        _apply_translations_to_posts(trending_posts, current_locale)
        try:
            t = PostTranslation.objects.filter(
                post=post, language=current_locale
            ).first()
            if t:
                post.title = t.title or post.title
                post.summary = t.summary or post.summary
                post.body = t.body or post.body
                post.seo_title = t.seo_title or post.seo_title
                post.seo_description = t.seo_description or post.seo_description
        except Exception:  # noqa: S110
            pass
        try:
            if post.category:
                ct = CategoryTranslation.objects.filter(
                    category=post.category, language=current_locale
                ).first()
                if ct:
                    post.category.name = ct.name or post.category.name
        except Exception:  # noqa: S110
            pass
        # tags
        try:
            translated_tags = {}
            for tag in post.curated_tags:  # type: ignore[attr-defined]
                tt = TagTranslation.objects.filter(
                    tag=tag, language=current_locale
                ).first()
                if tt:
                    translated_tags[tag.id] = tt.name
            if translated_tags:
                for tag in post.curated_tags:  # type: ignore[attr-defined]
                    if tag.id in translated_tags:
                        tag.name = translated_tags[tag.id]
        except Exception:  # noqa: S110
            pass

    # Get comments for the post
    from apps.comments.models import Comment

    comments = (
        Comment.objects.filter(
            post=post,
            is_deleted=False,
            parent__isnull=True,  # Top-level comments only, replies are nested
        )
        .select_related("user")
        .order_by("-created_at")
    )

    # Get tag categories for the tags widget
    tag_categories = Tag.CATEGORY_CHOICES if hasattr(Tag, "CATEGORY_CHOICES") else []  # type: ignore[attr-defined]

    # Get popular tags for sidebar
    popular_tags = Tag.objects.filter(
        is_active=True, is_curated=True, is_deleted=False
    ).order_by("-usage_count")[:10]

    # Get related posts based on tags
    related_posts = (
        Post.objects.filter(
            tags__in=post.curated_tags,  # type: ignore[attr-defined]
            status=PostStatus.PUBLISHED,
            publish_at__lte=timezone.now(),
        )
        .exclude(pk=post.pk)
        .distinct()
        .order_by("-published_at")[:3]
    )

    # If this is a firmware-linked post, fetch live firmware data
    firmware_table_data = None
    flashing_tools = None
    if post.firmware_model_id:  # type: ignore[attr-defined]
        from apps.firmwares.public_views import get_all_firmwares_for_model

        firmware_table_data = get_all_firmwares_for_model(post.firmware_model)
        # Fetch recommended flashing tools for this brand/chipset
        try:
            from apps.firmwares.models import FlashingTool

            brand = post.firmware_brand
            if brand:
                tools_qs = (
                    FlashingTool.objects.filter(is_active=True, supported_brands=brand)
                    .select_related("category")
                    .distinct()[:12]
                )
                flashing_tools = list(tools_qs)
                if not flashing_tools:
                    # Fallback: featured tools
                    flashing_tools = list(
                        FlashingTool.objects.filter(
                            is_active=True, is_featured=True
                        ).select_related("category")[:8]
                    )
        except Exception:  # noqa: S110
            pass
    elif post.firmware_brand_id:  # type: ignore[attr-defined]
        # Brand-only posts (no specific model)
        try:
            from apps.firmwares.models import FlashingTool

            flashing_tools = list(
                FlashingTool.objects.filter(
                    is_active=True, supported_brands=post.firmware_brand
                )
                .select_related("category")
                .distinct()[:12]
            )
        except Exception:  # noqa: S110
            pass

    # Ad gate: check if this post requires watching an ad before reading
    ad_gate_required = False
    ad_gate_config = None
    if post.ad_gate_config_id:  # type: ignore[attr-defined]
        auto_triggered = (
            post.ad_gate_min_views > 0 and post.views_count >= post.ad_gate_min_views
        )
        is_staff = getattr(request.user, "is_staff", False)
        session_key = f"ad_gate_{post.slug}"
        if (
            (post.ad_gate_enabled or auto_triggered)
            and not request.session.get(session_key)
            and not is_staff
        ):
            ad_gate_required = True
            ad_gate_config = post.ad_gate_config

    post_body_html = post.body or ""

    return render(
        request,
        "blog/post_detail.html",
        {
            "post": post,
            "comments": comments,
            "related_posts": related_posts,
            "related_widget_html": related_widget_html,
            "trending_tags": trending_tags,
            "trending_posts": trending_posts,
            "popular_tags": popular_tags,
            "tag_categories": tag_categories,
            "allow_user_posts": allow_user_posts,
            "tag_cloud": tag_cloud_raw,
            "canonical_url": canonical,
            "current_locale": current_locale,
            "firmware_table_data": firmware_table_data,
            "flashing_tools": flashing_tools,
            "ad_gate_required": ad_gate_required,
            "ad_gate_config": ad_gate_config,
            "post_body_html": post_body_html,
        },
    )


@require_GET
def ad_gate_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Return the ad-gate HTMX fragment for a blog post.

    If the gate is no longer required (staff, already unlocked), redirect
    to the full post instead of rendering the fragment.
    """
    post = get_object_or_404(Post, slug__iexact=slug)
    session_key = f"ad_gate_{post.slug}"
    is_staff = getattr(request.user, "is_staff", False)

    if is_staff or request.session.get(session_key) or not post.ad_gate_config_id:  # type: ignore[attr-defined]
        return redirect("blog:post_detail", slug=post.slug)

    return render(
        request,
        "blog/fragments/ad_gate.html",
        {"post": post, "ad_gate_config": post.ad_gate_config},
    )


@require_POST
def ad_gate_complete(request: HttpRequest, slug: str) -> HttpResponse:
    """Mark the ad gate as completed for a blog post.

    Sets a session flag so the user can read the full article.
    Tracks the view in ``RewardedAdView`` if user is authenticated.
    """
    post = get_object_or_404(Post, slug__iexact=slug)
    session_key = f"ad_gate_{post.slug}"

    # Record rewarded view for authenticated users
    if request.user.is_authenticated and post.ad_gate_config_id:  # type: ignore[attr-defined]
        try:
            from apps.ads.models import RewardedAdView

            today_count = RewardedAdView.objects.filter(
                user=request.user,
                config=post.ad_gate_config,
                viewed_at__date=timezone.now().date(),
            ).count()
            daily_limit = (
                post.ad_gate_config.daily_limit_per_user  # type: ignore[union-attr]
                if post.ad_gate_config
                else 5
            )
            if today_count < daily_limit:
                RewardedAdView.objects.create(
                    user=request.user,
                    config=post.ad_gate_config,
                    completed=True,
                    credits_earned=getattr(post.ad_gate_config, "reward_amount", 0),
                )
        except Exception:  # noqa: S110
            pass

    request.session[session_key] = True
    return redirect("blog:post_detail", slug=post.slug)


@require_GET
def post_archive_year(request: HttpRequest, year: int) -> HttpResponse:
    posts = (
        Post.objects.filter(
            status=PostStatus.PUBLISHED,
            publish_at__year=year,
            publish_at__lte=timezone.now(),
        )
        .select_related("author", "category")
        .prefetch_related("tags")
    )
    current_locale = resolve_locale(request, "blog")
    _apply_translations_to_posts(posts, current_locale)
    return render(
        request,
        "blog/archive_year.html",
        {"posts": posts, "year": year, "current_locale": current_locale},
    )


@require_GET
def post_archive_month(request: HttpRequest, year: int, month: int) -> HttpResponse:
    posts = (
        Post.objects.filter(
            status=PostStatus.PUBLISHED,
            publish_at__year=year,
            publish_at__month=month,
            publish_at__lte=timezone.now(),
        )
        .select_related("author", "category")
        .prefetch_related("tags")
    )
    current_locale = resolve_locale(request, "blog")
    _apply_translations_to_posts(posts, current_locale)
    return render(
        request,
        "blog/archive_month.html",
        {
            "posts": posts,
            "year": year,
            "month": month,
            "current_locale": current_locale,
        },
    )


@require_GET
def posts_api_public(request: HttpRequest) -> JsonResponse:
    """
    Lightweight public posts API (read-only).
    """
    qs = (
        Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=timezone.now())
        .select_related("author", "category")
        .prefetch_related("tags")
        .order_by("-published_at")[:50]
    )
    current_locale = resolve_locale(request, "blog")
    _apply_translations_to_posts(qs, current_locale)
    data = []
    for p in qs:
        data.append(
            {
                "title": p.title,
                "slug": p.slug,
                "summary": p.summary,
                "url": request.build_absolute_uri(p.get_absolute_url()),
                "category": p.category.name if p.category else None,
                "tags": list(
                    p.tags.filter(
                        is_curated=True, is_active=True, is_deleted=False
                    ).values_list("name", flat=True)
                ),
                "published_at": p.published_at.isoformat() if p.published_at else None,
            }
        )
    return JsonResponse({"items": data})


@login_required
def manage_posts(request: HttpRequest) -> HttpResponse:
    """
    Simple manager view for drafts and scheduled posts.
    Staff sees all; authors see their own.
    """
    settings_snapshot = _get_site_settings_snapshot()
    try:
        blog_api = AppService.get("blog")
        blog_settings = (
            blog_api.get_settings()
            if blog_api and hasattr(blog_api, "get_settings")
            else {}
        )
    except Exception:
        blog_settings = {}
    allow_user_posts = blog_settings.get(
        "allow_user_blog_posts", settings_snapshot.get("allow_user_blog_posts", False)
    )
    if not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or allow_user_posts
    ):
        raise Http404

    base_qs = Post.objects.select_related("author", "category")
    if getattr(request.user, "is_staff", False) or getattr(
        request.user, "is_superuser", False
    ):
        drafts = base_qs.filter(status=PostStatus.DRAFT).order_by(
            "-updated_at", "-created_at"
        )[:50]
        scheduled = base_qs.filter(status=PostStatus.SCHEDULED).order_by("publish_at")[
            :50
        ]
    else:
        drafts = base_qs.filter(author=request.user, status=PostStatus.DRAFT).order_by(
            "-updated_at", "-created_at"
        )[:50]
        scheduled = base_qs.filter(
            author=request.user, status=PostStatus.SCHEDULED
        ).order_by("publish_at")[:50]

    return render(
        request,
        "blog/manage.html",
        {
            "drafts": drafts,
            "scheduled": scheduled,
            "allow_user_posts": allow_user_posts,
        },
    )


def _can_manage_post(user, post: Post) -> bool:
    return bool(
        getattr(user, "is_staff", False)
        or getattr(user, "is_superuser", False)
        or post.author_id == user.pk
        or getattr(user, "has_role", lambda *roles: False)(CustomUser.Roles.EDITOR)
    )


@login_required
def post_history(request: HttpRequest, slug: str) -> HttpResponse:
    post = get_object_or_404(Post, slug__iexact=slug)
    if not _can_manage_post(request.user, post):
        raise Http404
    return render(
        request,
        "blog/post_history.html",
        {
            "post": post,
            "revisions": post.revisions.all().order_by("-created_at", "-pk"),
            "can_restore": post.status == PostStatus.DRAFT,
        },
    )


@login_required
@require_POST
def restore_post_revision(
    request: HttpRequest, slug: str, revision_id: int
) -> HttpResponse:
    post = get_object_or_404(Post, slug__iexact=slug)
    if not _can_manage_post(request.user, post):
        raise Http404
    revision = get_object_or_404(PostRevision, pk=revision_id, post=post)
    try:
        PostHistoryService.restore_revision(post, revision, user=request.user)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, "Draft restored from the selected version.")
    return redirect("blog:post_history", slug=post.slug)


@login_required
@require_POST
def bulk_publish(request: HttpRequest) -> HttpResponse:
    """
    Bulk publish selected posts only after their governance review is approved.
    """
    ids = request.POST.getlist("post_id")
    if not ids:
        messages.info(request, "No posts selected.")
        return redirect("blog:manage_posts")
    qs = Post.objects.filter(pk__in=ids)
    updated = 0
    rejected = 0
    for post in qs:
        if post.author_id != request.user.pk and not request.user.is_staff:
            rejected += 1
            continue
        try:
            workflow.publish(post, user=request.user)
        except (PermissionDenied, ValidationError):
            rejected += 1
        else:
            updated += 1
    try:
        log_event(
            logger,
            "info",
            "blog.bulk_publish",
            actor=request.user.pk,
            count=updated,
            ids=",".join(ids),
        )
    except Exception:
        logger.debug("bulk_publish audit log failed", exc_info=True)
    if rejected:
        messages.warning(
            request,
            f"Published {updated} posts; {rejected} were not eligible or lacked permission.",
        )
    else:
        messages.success(request, f"Published {updated} posts.")
    return redirect("blog:manage_posts")


@login_required
@require_http_methods(["GET", "POST"])
def post_create(request: HttpRequest) -> HttpResponse:
    settings_snapshot = _get_site_settings_snapshot()
    try:
        blog_api = AppService.get("blog")
        blog_settings = (
            blog_api.get_settings()
            if blog_api and hasattr(blog_api, "get_settings")
            else {}
        )
    except Exception:
        blog_api = None
        blog_settings = {}
    blog_enabled = blog_settings.get(
        "enable_blog",
        False if blog_api is None else settings_snapshot.get("enable_blog", True),
    )
    if not blog_enabled and not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
    ):
        raise Http404("Blog is disabled.")

    allow_user_posts = blog_settings.get(
        "allow_user_blog_posts", settings_snapshot.get("allow_user_blog_posts", False)
    )
    # RBAC: allow staff, editors, authors; optionally allow authenticated users if toggle enabled.
    allowed = (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or getattr(request.user, "has_role", lambda *r: False)(
            CustomUser.Roles.EDITOR, CustomUser.Roles.AUTHOR
        )
    )
    if not allowed and not (allow_user_posts and request.user.is_authenticated):
        raise Http404

    edit_slug = request.GET.get("edit") or request.POST.get("edit")
    instance = None
    if edit_slug:
        instance = get_object_or_404(Post, slug__iexact=edit_slug)
        if not _can_manage_post(request.user, instance):
            raise Http404

    if request.method == "POST":
        form = PostForm(request.POST, instance=instance)
        if form.is_valid():
            values = dict(form.cleaned_data)
            tags = values.pop("tags")
            with transaction.atomic():
                if instance:
                    post = PostHistoryService.update_post(
                        instance,
                        user=request.user,
                        change_summary="Edited in the editorial workspace",
                        tags=tags,
                        **values,
                    )
                else:
                    post = form.save(commit=False)
                    post.author = request.user
                    post.status = PostStatus.DRAFT
                    post.publish_at = None
                    post.published_at = None
                    post.is_published = False
                    post.featured = False
                    post.save()
                    form.save_m2m()
                # If no tags supplied, auto-suggest from content
                if not post.tags.exists():
                    tag_services.auto_tag_post(post, allow_create=True, max_tags=5)  # type: ignore[attr-defined]
                _sync_tag_usage(post.tags.all())
                if not instance:
                    PostHistoryService.record_current_version(
                        post,
                        user=request.user,
                        change_summary="Initial version",
                    )
            messages.success(request, "Post updated." if instance else "Post saved.")
            return redirect("blog:post_detail", slug=post.slug)
    else:
        form = PostForm(instance=instance)
    return render(
        request,
        "blog/post_form.html",
        {"form": form, "editing": bool(instance), "editing_post": instance},
    )


def category_create(request: HttpRequest) -> HttpResponse:
    """
    Allow authorized users (staff or if allow_user_posts is on) to create categories.
    """
    settings_snapshot = _get_site_settings_snapshot()
    try:
        blog_api = AppService.get("blog")
        blog_settings = (
            blog_api.get_settings()
            if blog_api and hasattr(blog_api, "get_settings")
            else {}
        )
    except Exception:
        blog_api = None
        blog_settings = {}

    blog_enabled = blog_settings.get(
        "enable_blog",
        False if blog_api is None else settings_snapshot.get("enable_blog", True),
    )
    if not blog_enabled and not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
    ):
        raise Http404("Blog is disabled.")

    allow_user_posts = blog_settings.get(
        "allow_user_blog_posts", settings_snapshot.get("allow_user_blog_posts", False)
    )

    # RBAC: allow staff, editors, authors; optionally allow authenticated users if toggle enabled.
    allowed = (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or getattr(request.user, "has_role", lambda *r: False)(
            CustomUser.Roles.EDITOR, CustomUser.Roles.AUTHOR
        )
    )
    if not allowed and not (allow_user_posts and request.user.is_authenticated):
        raise Http404

    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Category '{cat.name}' created.")
            return redirect("blog:post_list")
    else:
        form = CategoryForm()

    return render(request, "blog/category_form.html", {"form": form})


def api_posts(request: HttpRequest) -> JsonResponse:
    """
    Lightweight JSON listing for widgets/search.
    """
    posts = Post.objects.filter(
        status=PostStatus.PUBLISHED, publish_at__lte=timezone.now()
    )
    q = request.GET.get("q", "").strip()
    if q:
        posts = posts.filter(Q(title__icontains=q) | Q(summary__icontains=q))
    posts = posts.select_related("author").order_by("-published_at")[:20]
    items = [
        {
            "title": p.title,
            "slug": p.slug,
            "author": str(p.author),
            "published_at": p.published_at.isoformat() if p.published_at else None,
        }
        for p in posts
    ]
    return JsonResponse({"items": items})


def api_related(request: HttpRequest, slug: str) -> JsonResponse:
    post = get_object_or_404(
        Post, slug=slug, status=PostStatus.PUBLISHED, publish_at__lte=timezone.now()
    )
    related = (
        Post.objects.filter(
            tags__in=post.tags.filter(
                is_curated=True, is_active=True, is_deleted=False
            ),
            status=PostStatus.PUBLISHED,
            publish_at__lte=timezone.now(),
        )
        .exclude(pk=post.pk)
        .distinct()
        .order_by("-published_at")[:5]
    )
    items = [{"title": p.title, "slug": p.slug} for p in related]
    return JsonResponse({"items": items})


@login_required
@require_POST
def post_autosave(request: HttpRequest) -> JsonResponse:
    """
    Autosave stub for editor. Uses cache to persist the last payload per-user for 10 minutes.
    """
    post_id = request.POST.get("post_id")
    data = {
        "title": request.POST.get("title", ""),
        "summary": request.POST.get("summary", ""),
        "body": request.POST.get("body", ""),
        "tags": request.POST.getlist("tags"),
        "updated_at": timezone.now().isoformat(),
    }
    draft, _ = PostDraft.objects.update_or_create(
        user=request.user,
        post_id=post_id or None,
        defaults={"data": data},
    )
    return JsonResponse(
        {"ok": True, "message": "Autosave stored", "draft_id": draft.pk, "data": data}
    )


@login_required
@require_POST
def post_preview(request: HttpRequest) -> JsonResponse:
    """
    Preview stub: echoes body/summary; replace with markdown rendering as needed.
    """
    body = request.POST.get("body", "")
    summary = request.POST.get("summary", "")
    rendered_body = body
    try:
        import markdown

        rendered_body = markdown.markdown(body, extensions=["fenced_code", "tables"])
    except Exception:  # noqa: S110
        pass
    return JsonResponse({"ok": True, "body": rendered_body, "summary": summary})


def widget_trending_tags(request: HttpRequest) -> JsonResponse:
    tags = Tag.objects.filter(
        is_active=True, is_curated=True, is_deleted=False
    ).order_by("-usage_count", "name")[:10]
    items = [
        {"name": t.name, "slug": t.slug, "usage_count": t.usage_count} for t in tags
    ]
    return JsonResponse({"items": items})


def widget_latest_posts(request: HttpRequest) -> JsonResponse:
    posts = (
        Post.objects.filter(status=PostStatus.PUBLISHED)
        .select_related("author")
        .order_by("-published_at")[:5]
    )
    items = [
        {
            "title": p.title,
            "slug": p.slug,
            "published_at": p.published_at.isoformat() if p.published_at else None,
        }
        for p in posts
    ]
    return JsonResponse({"items": items})


def widget_top_posts(request: HttpRequest) -> JsonResponse:
    posts = (
        Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=timezone.now())
        .select_related("author")
        .order_by("-published_at")[:5]
    )
    items = [
        {
            "title": p.title,
            "slug": p.slug,
            "published_at": p.published_at.isoformat() if p.published_at else None,
        }
        for p in posts
    ]
    return JsonResponse({"items": items})


@login_required
@require_POST
def api_ai_assist(request: HttpRequest) -> JsonResponse:
    """
    AI assist endpoint for the blog editor.
    Actions: generate_title, summarize, improve, translate (stub), suggest_seo.
    """
    action = request.POST.get("action") or ""
    text = request.POST.get("text") or ""
    context = request.POST.get("context") or ""
    if not action:
        return JsonResponse({"ok": False, "error": "missing_action"}, status=400)

    try:
        if action == "generate_title":
            suggestion = ai_client.generate_title(text, request.user)
            return JsonResponse({"ok": True, "suggestion": suggestion[:240]})
        if action == "summarize":
            summary = ai_client.summarize_text(text or context, request.user)
            return JsonResponse({"ok": True, "suggestion": summary})
        if action == "improve":
            improved = ai_editor.rewrite_paragraph(text or context, tone="concise")
            return JsonResponse({"ok": True, "suggestion": improved})
        if action == "suggest_seo":
            desc = ai_client.generate_seo_description(text or context, request.user)
            return JsonResponse({"ok": True, "suggestion": desc})
        if action == "outline":
            outline = ai_editor.suggest_outline(text or context)
            return JsonResponse({"ok": True, "suggestion": outline})
        if action == "suggest_tags":
            tags_payload = ai_editor.suggest_tags(text or context)
            return JsonResponse({"ok": True, **tags_payload})
        return JsonResponse({"ok": False, "error": "unknown_action"}, status=400)
    except PermissionDenied as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=403)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages}, status=400)
    except GovernanceError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=409)


@login_required
@require_POST
def api_workflow(request: HttpRequest, slug: str) -> JsonResponse:
    """
    Workflow transitions for posts (request_review, schedule, publish, archive).
    """
    action = request.POST.get("action")
    when = request.POST.get("when")
    post = get_object_or_404(Post, slug=slug)
    if not action:
        return JsonResponse({"ok": False, "error": "missing_action"}, status=400)
    if action not in {"request_review", "schedule", "publish", "archive"}:
        return JsonResponse({"ok": False, "error": "invalid_action"}, status=400)
    try:
        if action == "request_review":
            res = workflow.request_review(post, user=request.user)
        elif action == "schedule":
            when_dt = datetime.datetime.fromisoformat(when) if when else timezone.now()
            res = workflow.schedule(post, when=when_dt, user=request.user)
        elif action == "publish":
            res = workflow.publish(post, user=request.user)
        else:
            res = workflow.archive(post, user=request.user)
        log_event(
            logger,
            "info",
            "blog.workflow.transition",
            action=action,
            post=post.slug,
            user=request.user.pk,
            status=res.status,
            correlation_id=getattr(request, "correlation_id", None),
        )
        return JsonResponse(
            {"ok": res.ok, "status": res.status, "message": res.message}
        )
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@require_GET
def api_similar_posts(request: HttpRequest) -> JsonResponse:
    """
    Suggest existing posts similar to a title/summary for collision checks and inspiration.
    """
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"items": []})
    posts = (
        Post.objects.filter(Q(title__icontains=q) | Q(summary__icontains=q))
        .select_related("author")
        .order_by("-published_at")[:8]
    )
    items = [
        {
            "title": p.title,
            "slug": p.slug,
            "published_at": p.published_at.isoformat() if p.published_at else None,
        }
        for p in posts
    ]
    return JsonResponse({"items": items})


@login_required
@require_POST
def post_like(request: HttpRequest, pk: int) -> JsonResponse:
    """
    Like/unlike a blog post.
    """
    try:
        post = get_object_or_404(Post, pk=pk)

        # Check if user has already liked this post
        # Using a simple approach - you may want to create a PostLike model later
        liked_posts = request.session.get("liked_posts", [])

        if pk in liked_posts:
            # Unlike
            liked_posts.remove(pk)
            post.likes_count = max(0, (post.likes_count or 0) - 1)
            action = "unliked"
        else:
            # Like
            liked_posts.append(pk)
            post.likes_count = (post.likes_count or 0) + 1
            action = "liked"

        request.session["liked_posts"] = liked_posts
        post.save(update_fields=["likes_count"])

        return JsonResponse(
            {"ok": True, "action": action, "likes_count": post.likes_count}
        )
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


@login_required
@require_POST
def post_bookmark(request: HttpRequest, pk: int) -> JsonResponse:
    """
    Bookmark/unbookmark a blog post.
    """
    try:
        get_object_or_404(Post, pk=pk)

        # Check if user has already bookmarked this post
        bookmarked_posts = request.session.get("bookmarked_posts", [])

        if pk in bookmarked_posts:
            # Remove bookmark
            bookmarked_posts.remove(pk)
            action = "unbookmarked"
        else:
            # Add bookmark
            bookmarked_posts.append(pk)
            action = "bookmarked"

        request.session["bookmarked_posts"] = bookmarked_posts
        request.session.modified = True

        return JsonResponse(
            {"ok": True, "action": action, "is_bookmarked": pk in bookmarked_posts}
        )
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)


# ============================================================
# Brand / Model Blog Pages (mirrors firmware URL hierarchy)
# ============================================================


@require_GET
def brand_posts(request: HttpRequest, brand_slug: str) -> HttpResponse:
    """Blog posts linked to a specific firmware brand."""
    from apps.firmwares.models import Brand

    brand = get_object_or_404(Brand, slug=brand_slug)
    now_ts = timezone.now()
    posts = (
        Post.objects.filter(
            firmware_brand=brand,
            status=PostStatus.PUBLISHED,
            publish_at__lte=now_ts,
        )
        .select_related("author", "category")
        .prefetch_related("tags")
        .order_by("-published_at")
    )

    paginator = Paginator(posts, 12)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Get other brands that have blog posts
    other_brands = (
        Brand.objects.filter(blog_posts__status=PostStatus.PUBLISHED)
        .exclude(pk=brand.pk)
        .distinct()
        .order_by("name")[:12]
    )

    context = {
        "brand": brand,
        "posts": page_obj,
        "other_brands": other_brands,
    }
    return render(request, "blog/brand_posts.html", context)


@require_GET
def model_posts(request: HttpRequest, brand_slug: str, model_slug: str) -> HttpResponse:
    """Blog posts linked to a specific firmware model."""
    from apps.firmwares.models import Brand
    from apps.firmwares.models import Model as DeviceModel

    brand = get_object_or_404(Brand, slug=brand_slug)
    model = get_object_or_404(DeviceModel, brand=brand, slug=model_slug)
    now_ts = timezone.now()
    posts = (
        Post.objects.filter(
            firmware_model=model,
            status=PostStatus.PUBLISHED,
            publish_at__lte=now_ts,
        )
        .select_related("author", "category")
        .prefetch_related("tags")
        .order_by("-published_at")
    )

    paginator = Paginator(posts, 12)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    context = {
        "brand": brand,
        "model": model,
        "posts": page_obj,
    }
    return render(request, "blog/model_posts.html", context)
