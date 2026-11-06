from __future__ import annotations

from difflib import SequenceMatcher

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.blog import selectors as blog_selectors
from apps.blog.models import Post
from apps.tags.api import get_settings as tag_settings
from apps.tags.models import Tag
from apps.tags.models_keyword import KeywordSuggestion
from apps.tags.selectors import public_tags, tag_cloud, with_live_post_counts
from apps.tags.services import automation, heuristics
from apps.users.services.rate_limit import allow_action

TAGS_PER_PAGE = 36
POSTS_PER_PAGE = 12
MAX_DUPLICATE_SCAN = 300


def _require_tag_staff(user) -> None:
    if not (
        user.is_authenticated and user.is_staff and user.has_perm("tags.change_tag")
    ):
        raise PermissionDenied("Tag management permission is required.")


def _bounded_float(raw: str | None, default: float, low: float, high: float) -> float:
    try:
        return min(high, max(low, float(raw))) if raw else default
    except ValueError:
        return default


def _bounded_int(raw: str | None, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(raw))) if raw else default
    except ValueError:
        return default


def similar_pairs(threshold: float, limit: int) -> list[dict]:
    """Near-duplicate tag pairs among the most used tags (bounded O(n²) scan)."""
    tags = list(
        Tag.objects.filter(
            is_active=True, is_deleted=False, merge_into__isnull=True
        ).order_by("-usage_count", "name")[:MAX_DUPLICATE_SCAN]
    )
    pairs: list[dict] = []
    for index, first in enumerate(tags):
        for second in tags[index + 1 :]:
            similarity = max(
                SequenceMatcher(
                    None, first.normalized_name, second.normalized_name
                ).ratio(),
                _jaccard(first.normalized_name, second.normalized_name),
            )
            if similarity >= threshold:
                pairs.append(
                    {
                        "score": round(similarity, 2),
                        "a": first.name,
                        "a_slug": first.slug,
                        "b": second.name,
                        "b_slug": second.slug,
                    }
                )
    pairs.sort(key=lambda pair: pair["score"], reverse=True)
    return pairs[:limit]


def _jaccard(a: str, b: str) -> float:
    first, second = set(a.lower().split()), set(b.lower().split())
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


@require_GET
def search(request: HttpRequest) -> JsonResponse:
    """Autocomplete over public tags."""
    query = request.GET.get("q", "").strip()[:80]
    tags = public_tags()
    if query:
        tags = tags.filter(
            Q(name__icontains=query)
            | Q(normalized_name__icontains=query)
            | Q(synonyms_text__icontains=query)
        )
    items = [
        {
            "name": tag.name,
            "slug": tag.slug,
            "usage_count": tag.usage_count,
            "synonyms": tag.synonyms,
            "description": tag.description,
            "is_curated": tag.is_curated,
        }
        for tag in tags.order_by("-usage_count", "name")[:20]
    ]
    return JsonResponse({"items": items})


@require_GET
def tag_list(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:80]
    tags = with_live_post_counts(public_tags())
    if query:
        tags = tags.filter(
            Q(name__icontains=query)
            | Q(normalized_name__icontains=query)
            | Q(synonyms_text__icontains=query)
        )
    page_obj = Paginator(tags.order_by("-live_count", "name"), TAGS_PER_PAGE).get_page(
        request.GET.get("page") or 1
    )
    context = {
        "tags": page_obj,
        "page_obj": page_obj,
        "q": query,
        "cloud": tag_cloud(24),
        "tag_settings": tag_settings(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "tags/partials/tag_results.html", context)
    return render(request, "tags/list.html", context)


@require_GET
def tag_detail(request: HttpRequest, slug: str) -> HttpResponse:
    tag = get_object_or_404(public_tags(), slug=slug)
    posts = blog_selectors.live_posts().filter(tags=tag).order_by("-published_at")
    page_obj = Paginator(posts, POSTS_PER_PAGE).get_page(request.GET.get("page") or 1)
    related_tags = list(
        with_live_post_counts(public_tags().filter(posts__tags=tag).exclude(pk=tag.pk))
        .filter(live_count__gt=0)
        .order_by("-live_count", "name")[:12]
    )
    context = {
        "tag": tag,
        "cards": blog_selectors.build_cards(page_obj.object_list),
        "page_obj": page_obj,
        "related_tags": related_tags,
        "latest_posts": blog_selectors.build_cards(blog_selectors.latest_posts(4)),
        "cloud": tag_cloud(16),
        "live_count": page_obj.paginator.count,
        "tag_settings": tag_settings(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "tags/partials/tag_posts.html", context)
    return render(request, "tags/detail.html", context)


@login_required
@require_GET
def manage_tags(request: HttpRequest) -> HttpResponse:
    _require_tag_staff(request.user)
    suggestions = Tag.objects.filter(
        is_curated=False, is_deleted=False, merge_into__isnull=True, ai_suggested=True
    ).order_by("-created_at")[:50]
    top_tags = with_live_post_counts(
        Tag.objects.filter(is_active=True, is_deleted=False, merge_into__isnull=True)
    ).order_by("-live_count", "name")[:50]
    totals = Tag.objects.filter(is_deleted=False).aggregate(
        total=Count("pk"),
        curated=Count("pk", filter=Q(is_curated=True)),
        pending=Count("pk", filter=Q(is_curated=False, ai_suggested=True)),
    )
    return render(
        request,
        "tags/manage.html",
        {
            "suggestions": suggestions,
            "top_tags": top_tags,
            "totals": totals,
            "tag_settings": tag_settings(),
        },
    )


@login_required
@require_POST
def review_tag(request: HttpRequest) -> HttpResponse:
    """Staff approve or reject one suggested tag; approval attaches it to matching posts."""
    _require_tag_staff(request.user)
    tag = get_object_or_404(Tag, pk=request.POST.get("tag_id"), is_deleted=False)
    decision = request.POST.get("decision")
    if decision == "approve":
        tag.is_curated = True
        tag.is_active = True
        tag.ai_suggested = False
        tag.save(
            update_fields=["is_curated", "is_active", "ai_suggested", "updated_at"]
        )
        from apps.tags.tasks import reattach_curated_tag

        transaction.on_commit(lambda: reattach_curated_tag.delay(tag.pk))
        messages.success(request, f"Approved “{tag.name}”.")
    elif decision == "reject":
        tag.is_curated = False
        tag.is_active = False
        tag.save(update_fields=["is_curated", "is_active", "updated_at"])
        messages.success(request, f"Rejected “{tag.name}”.")
    else:
        messages.error(request, "Choose approve or reject.")
    return redirect("tags:manage")


@login_required
@require_http_methods(["GET", "POST"])
def merge_tags(request: HttpRequest) -> HttpResponse:
    """Two-step merge: choose the tags, review the impact, then confirm."""
    _require_tag_staff(request.user)
    source_slug = (request.POST or request.GET).get("source", "").strip()
    target_slug = (request.POST or request.GET).get("target", "").strip()
    if not source_slug or not target_slug:
        messages.error(request, "Choose both a source and a destination tag.")
        return redirect("tags:manage")
    if source_slug == target_slug:
        messages.error(request, "A tag cannot be merged into itself.")
        return redirect("tags:manage")
    source = get_object_or_404(Tag, slug=source_slug, is_deleted=False)
    target = get_object_or_404(
        Tag, slug=target_slug, is_deleted=False, merge_into__isnull=True
    )
    if request.method == "POST" and request.POST.get("confirm") == "1":
        automation.merge_tags(source, target, user=request.user)
        messages.success(request, f"Merged “{source.name}” into “{target.name}”.")
        return redirect("tags:manage")
    return render(
        request,
        "tags/merge_confirm.html",
        {
            "source": source,
            "target": target,
            "affected": Post.objects.filter(tags=source).count(),
        },
    )


@require_GET
def tag_analytics(request: HttpRequest) -> JsonResponse:
    rows = (
        with_live_post_counts(public_tags())
        .order_by("-live_count", "name")
        .values("name", "live_count")[:50]
    )
    return JsonResponse(
        {"items": [{"name": row["name"], "usage": row["live_count"]} for row in rows]}
    )


@staff_member_required(login_url="admin:login")
@require_GET
def search_duplicates(request: HttpRequest) -> JsonResponse:
    _require_tag_staff(request.user)
    threshold = _bounded_float(request.GET.get("threshold"), 0.7, 0.3, 1.0)
    limit = _bounded_int(request.GET.get("limit"), 200, 1, 500)
    return JsonResponse({"items": similar_pairs(threshold, limit)})


@staff_member_required(login_url="admin:login")
@require_GET
def duplicates_review(request: HttpRequest) -> HttpResponse:
    _require_tag_staff(request.user)
    return render(
        request, "tags/admin_duplicates.html", {"pairs": similar_pairs(0.7, 100)}
    )


@staff_member_required(login_url="admin:login")
@require_GET
def keyword_review(request: HttpRequest) -> HttpResponse:
    _require_tag_staff(request.user)
    suggestions = KeywordSuggestion.objects.select_related("provider").order_by(
        "-score", "-created_at"
    )[:200]
    return render(
        request, "tags/admin_keyword_review.html", {"suggestions": suggestions}
    )


@staff_member_required(login_url="admin:login")
@require_POST
def apply_keyword(request: HttpRequest) -> HttpResponse:
    _require_tag_staff(request.user)
    keyword = get_object_or_404(
        KeywordSuggestion.objects.select_related("provider"), pk=request.POST.get("id")
    )
    tag = automation.ensure_curated_tag(keyword.keyword)
    if tag is None:
        messages.error(request, "That keyword cannot be used as a tag name.")
        return redirect("tags:keyword_review")
    keyword.delete()
    messages.success(request, f"Created tag “{tag.name}” from the suggestion.")
    return redirect("tags:keyword_review")


@login_required
@require_POST
def suggest_tags(request: HttpRequest) -> JsonResponse:
    """Keyword suggestions for editors; nothing is attached or created here."""
    if not tag_settings()["allow_public_suggestions"]:
        return JsonResponse({"ok": False, "error": "suggestions_disabled"}, status=403)
    text = (request.POST.get("text") or "").strip()[:8000]
    if not text:
        return JsonResponse({"ok": False, "error": "empty"}, status=400)
    if not allow_action(f"tags:suggest:{request.user.pk}", 5, window_seconds=60):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    suggestions = heuristics.suggest_tags_from_text(text, limit=10)
    existing = {
        tag.normalized_name: tag
        for tag in Tag.objects.filter(
            normalized_name__in=[item["normalized"] for item in suggestions]
        )
    }
    return JsonResponse(
        {
            "ok": True,
            "suggestions": [
                {
                    "name": item["name"],
                    "slug": getattr(existing.get(item["normalized"]), "slug", ""),
                    "exists": item["normalized"] in existing,
                    "is_curated": bool(
                        getattr(existing.get(item["normalized"]), "is_curated", False)
                    ),
                }
                for item in suggestions
            ],
        }
    )
