from __future__ import annotations

from difflib import SequenceMatcher

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.blog.models import Post, PostStatus
from apps.core.app_service import AppService

from . import services
from .models import Tag


def _get_tag_settings() -> dict:
    try:
        tags_api = AppService.get("tags")
        if tags_api and hasattr(tags_api, "get_settings"):
            return tags_api.get_settings()
    except Exception:  # noqa: S110
        pass
    return {
        "allow_public_suggestions": True,
        "enable_ai_suggestions": True,
        "show_tag_usage": True,
    }


@require_GET
def search(request):
    """
    Tag autocomplete/search endpoint.
    """
    q = request.GET.get("q", "").strip()
    qs = Tag.objects.filter(is_active=True, is_deleted=False)
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(normalized_name__icontains=q)
            | Q(synonyms_text__icontains=q)
        )
    qs = qs.order_by("-usage_count", "name")[:20]
    items = [
        {
            "name": t.name,
            "slug": t.slug,
            "usage_count": t.usage_count,
            "synonyms": t.synonyms,
            "description": t.description,
            "is_curated": t.is_curated,
        }
        for t in qs
    ]
    return JsonResponse({"items": items})


@require_GET
def tag_list(request: HttpRequest) -> HttpResponse:
    tags = Tag.objects.filter(is_active=True, is_deleted=False).order_by(
        "-usage_count", "name"
    )
    return render(
        request,
        "tags/list.html",
        {"tags": tags, "tag_settings": _get_tag_settings()},
    )


@require_GET
def tag_detail(request: HttpRequest, slug: str) -> HttpResponse:
    tag = get_object_or_404(Tag, slug=slug, is_deleted=False)
    now_ts = timezone.now()
    posts = (
        Post.objects.filter(
            tags=tag, status=PostStatus.PUBLISHED, publish_at__lte=now_ts
        )
        .select_related("author", "category")
        .prefetch_related("tags")
        .order_by("-published_at")
    )
    paginator = Paginator(posts, 10)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    trending_tags = Tag.objects.order_by("-usage_count")[:10]
    trending_tags = trending_tags.filter(is_deleted=False)
    latest = Post.objects.filter(
        status=PostStatus.PUBLISHED, publish_at__lte=now_ts
    ).order_by("-published_at")[:5]
    trending_tags_html = render_to_string(
        "components/tag_badges.html", {"tags": trending_tags}
    )
    latest_widget_html = render_to_string(
        "blog/partials/latest_widget.html", {"posts": latest}
    )
    return render(
        request,
        "tags/detail.html",
        {
            "tag": tag,
            "posts": page_obj.object_list,
            "page_obj": page_obj,
            "trending_tags_html": trending_tags_html,
            "latest_widget_html": latest_widget_html,
            "tag_settings": _get_tag_settings(),
        },
    )


@login_required
def manage_tags(request: HttpRequest) -> HttpResponse:
    if not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
    ):
        raise Http404
    top_tags = Tag.objects.filter(is_active=True, is_deleted=False).order_by(
        "-usage_count", "name"
    )[:50]
    curated = Tag.objects.filter(is_curated=True, is_deleted=False).order_by("name")[
        :50
    ]
    return render(
        request,
        "tags/manage.html",
        {
            "top_tags": top_tags,
            "curated_tags": curated,
            "tag_settings": _get_tag_settings(),
        },
    )


@login_required
@require_POST
def merge_tags(request: HttpRequest) -> JsonResponse:
    """
    Simple admin/staff merge: expects source_slug -> target_slug, reassign posts, delete source.
    """
    if not (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
    ):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    source_slug = request.POST.get("source")
    target_slug = request.POST.get("target")
    if not source_slug or not target_slug or source_slug == target_slug:
        return JsonResponse({"ok": False, "error": "invalid"}, status=400)
    source = get_object_or_404(Tag, slug=source_slug, is_deleted=False)
    target = get_object_or_404(Tag, slug=target_slug, is_deleted=False)
    posts = Post.objects.filter(tags=source)
    for p in posts:
        p.tags.add(target)
        p.tags.remove(source)
    source.is_deleted = True
    source.is_active = False
    source.save(update_fields=["is_deleted", "is_active"])
    target.usage_count = target.posts.count()  # type: ignore[attr-defined]
    target.save(update_fields=["usage_count"])
    return JsonResponse({"ok": True, "merged": source_slug, "into": target_slug})


@require_GET
def tag_analytics(request: HttpRequest) -> HttpResponse:
    tags = Tag.objects.order_by("-usage_count", "name")[:50]
    data = [{"name": t.name, "usage": t.usage_count} for t in tags]
    return JsonResponse({"items": data})


@require_GET
def search_duplicates(request: HttpRequest) -> JsonResponse:
    threshold = float(request.GET.get("threshold", "0.7") or "0.7")
    limit = int(request.GET.get("limit", "200") or "200")
    tags = list(Tag.objects.filter(is_active=True, is_deleted=False))
    pairs = []
    for i, t1 in enumerate(tags):
        for t2 in tags[i + 1 :]:
            from difflib import SequenceMatcher

            sim = max(
                SequenceMatcher(None, t1.normalized_name, t2.normalized_name).ratio(),
                services.jaccard(t1.normalized_name, t2.normalized_name),  # type: ignore[attr-defined]
            )
            if sim >= threshold:
                pairs.append({"score": round(sim, 2), "a": t1.name, "b": t2.name})
                if len(pairs) >= limit:
                    break
        if len(pairs) >= limit:
            break
    pairs.sort(key=lambda x: x["score"], reverse=True)
    return JsonResponse({"items": pairs})


@staff_member_required(login_url="admin_suite:admin_suite_login")
def duplicates_review(request: HttpRequest) -> HttpResponse:
    threshold = 0.7
    tags = list(Tag.objects.filter(is_active=True, is_deleted=False))
    pairs = []
    for i, t1 in enumerate(tags):
        for t2 in tags[i + 1 :]:
            sim = max(
                SequenceMatcher(None, t1.normalized_name, t2.normalized_name).ratio(),
                services.jaccard(t1.normalized_name, t2.normalized_name),  # type: ignore[attr-defined]
            )
            if sim >= threshold:
                pairs.append(
                    {
                        "score": round(sim, 2),
                        "a": t1.name,
                        "a_slug": t1.slug,
                        "b": t2.name,
                        "b_slug": t2.slug,
                    }
                )
    pairs.sort(key=lambda x: x["score"], reverse=True)
    return render(request, "tags/admin_duplicates.html", {"pairs": pairs})


@staff_member_required(login_url="admin_suite:admin_suite_login")
def keyword_review(request: HttpRequest) -> HttpResponse:
    suggestions = Tag.objects.none()
    from .models_keyword import KeywordSuggestion

    suggestions = KeywordSuggestion.objects.select_related("provider").order_by(
        "-score", "-created_at"
    )[:200]
    return render(
        request, "tags/admin_keyword_review.html", {"suggestions": suggestions}
    )


@staff_member_required(login_url="admin_suite:admin_suite_login")
@require_POST
def apply_keyword(request: HttpRequest) -> HttpResponse:
    from .models_keyword import KeywordSuggestion

    kw_id = request.POST.get("id")
    kw = KeywordSuggestion.objects.filter(pk=kw_id).select_related("provider").first()
    if not kw:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    name = kw.keyword[:64]
    norm = services._normalize(name)  # type: ignore[attr-defined]
    tag, created = Tag.objects.get_or_create(
        normalized_name=norm,
        defaults={
            "name": name,
            "slug": kw.keyword[:75],
            "description": f"Imported from provider {kw.provider.name}",
            "ai_suggested": True,
        },
    )
    kw.delete()
    return JsonResponse({"ok": True, "tag": tag.name, "created": created})


@login_required
@require_POST
def suggest_tags(request: HttpRequest) -> JsonResponse:
    """
    AI tag suggestions endpoint; requires explicit acceptance on the client.
    """
    text = (request.POST.get("text") or "").strip()
    if not text:
        return JsonResponse({"ok": False, "error": "empty"}, status=400)
    # Simple server-side throttle per user
    cache_key = f"tags:suggest:{request.user.pk}"
    try:
        from django.core.cache import cache

        count = cache.get(cache_key, 0)
        if count and int(count) >= 5:
            return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
        cache.set(cache_key, int(count) + 1, timeout=60)
    except Exception:  # noqa: S110
        pass
    try:
        suggestions = services.suggest_tags_from_text(text, limit=10)  # type: ignore[attr-defined]
        # Enrich with curated flag when existing
        names = [s["normalized"] for s in suggestions if s.get("normalized")]
        existing = {
            t.normalized_name: t for t in Tag.objects.filter(normalized_name__in=names)
        }
        enriched = []
        for s in suggestions:
            norm = s.get("normalized")
            tag_obj = existing.get(norm)
            enriched.append(
                {
                    "name": s["name"],
                    "slug": getattr(tag_obj, "slug", ""),
                    "exists": bool(tag_obj),
                    "is_curated": bool(getattr(tag_obj, "is_curated", False)),
                }
            )
        return JsonResponse({"ok": True, "suggestions": enriched})
    except Exception:
        return JsonResponse({"ok": False, "error": "failed"}, status=500)
