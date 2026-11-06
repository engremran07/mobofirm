"""JSON endpoints for the editor and lightweight public lookups."""

from __future__ import annotations

import logging
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404, HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.blog import selectors
from apps.blog.access import (
    blog_enabled_for,
    can_author_posts,
    can_edit_post,
    can_manage_post,
)
from apps.blog.models import Post, PostDraft, PostLike
from apps.blog.rendering import render_article
from apps.blog.services import ai_editor
from apps.blog.views_manage import WORKFLOW_ACTIONS, run_workflow
from apps.core import ai_client
from apps.core.utils.logging import log_event
from apps.users.services.rate_limit import allow_action

logger = logging.getLogger(__name__)

SESSION_BOOKMARKS_KEY = "bookmarked_posts"


def _public_items(posts) -> list[dict]:
    return [
        {
            "title": post.title,
            "slug": post.slug,
            "summary": post.summary,
            "author": selectors.author_name(post),
            "published_at": post.published_at.isoformat()
            if post.published_at
            else None,
        }
        for post in posts
    ]


@require_GET
def api_posts(request: HttpRequest) -> JsonResponse:
    """Published posts as JSON, optionally filtered by ``q`` (at most 20)."""
    if not blog_enabled_for(request.user):
        raise Http404
    filters = selectors.ListingFilters.from_request(request.GET)
    posts = selectors.filtered_posts(filters)[:20]
    return JsonResponse({"items": _public_items(posts)})


@require_GET
def api_related(request: HttpRequest, slug: str) -> JsonResponse:
    if not blog_enabled_for(request.user):
        raise Http404
    post = get_object_or_404(Post.objects.live(), slug=slug)
    return JsonResponse(
        {
            "items": [
                {"title": related.title, "slug": related.slug}
                for related in selectors.related_posts(post, 5)
            ]
        }
    )


@require_GET
def api_similar_posts(request: HttpRequest) -> JsonResponse:
    """Published posts resembling ``q``; drafts and reviews are never listed."""
    if not blog_enabled_for(request.user):
        raise Http404
    query = request.GET.get("q", "").strip()[:200]
    if not query:
        return JsonResponse({"items": []})
    posts = (
        selectors.live_posts()
        .filter(Q(title__icontains=query) | Q(summary__icontains=query))
        .order_by("-published_at")[:8]
    )
    return JsonResponse({"items": _public_items(posts)})


@login_required
@require_POST
def post_autosave(request: HttpRequest) -> JsonResponse:
    """Store the editor's latest unsaved payload for the current user."""
    post = None
    post_id = request.POST.get("post_id")
    if post_id:
        post = get_object_or_404(Post, pk=post_id)
        if not can_edit_post(request.user, post):
            raise Http404
    elif not can_author_posts(request.user):
        raise Http404
    data = {
        "title": request.POST.get("title", "")[:200],
        "summary": request.POST.get("summary", "")[:1000],
        "body": request.POST.get("body", "")[:50000],
        "tags": request.POST.getlist("tags")[:50],
        "updated_at": timezone.now().isoformat(),
    }
    draft, _ = PostDraft.objects.update_or_create(
        user_id=request.user.pk, post=post, defaults={"data": data}
    )
    return JsonResponse(
        {"ok": True, "message": "Autosave stored", "draft_id": draft.pk}
    )


@login_required
@require_POST
def post_preview(request: HttpRequest) -> JsonResponse:
    """Sanitised preview of the editor body (Markdown is rendered when available)."""
    if not can_author_posts(request.user):
        raise Http404
    body = request.POST.get("body", "")[:50000]
    try:
        import markdown
    except ImportError:
        rendered = body
    else:
        rendered = markdown.markdown(body, extensions=["fenced_code", "tables"])
    return JsonResponse(
        {
            "ok": True,
            "body": str(render_article(rendered).html),
            "summary": request.POST.get("summary", ""),
        }
    )


@login_required
@require_POST
def api_ai_assist(request: HttpRequest) -> JsonResponse:
    """Editor AI helpers: titles, summaries, rewrites, outlines, SEO text and tags."""
    if not can_author_posts(request.user):
        raise Http404
    if not allow_action(f"blog:ai:{request.user.pk}", 20, window_seconds=60):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    action = request.POST.get("action") or ""
    text = request.POST.get("text") or ""
    context = request.POST.get("context") or ""
    if not action:
        return JsonResponse({"ok": False, "error": "missing_action"}, status=400)
    source = text or context
    try:
        if action == "generate_title":
            return JsonResponse(
                {
                    "ok": True,
                    "suggestion": ai_client.generate_title(text, request.user)[:240],
                }
            )
        if action == "summarize":
            return JsonResponse(
                {
                    "ok": True,
                    "suggestion": ai_client.summarize_text(source, request.user),
                }
            )
        if action == "improve":
            return JsonResponse(
                {
                    "ok": True,
                    "suggestion": ai_editor.rewrite_paragraph(source, tone="concise"),
                }
            )
        if action == "suggest_seo":
            return JsonResponse(
                {
                    "ok": True,
                    "suggestion": ai_client.generate_seo_description(
                        source, request.user
                    ),
                }
            )
        if action == "outline":
            return JsonResponse(
                {"ok": True, "suggestion": ai_editor.suggest_outline(source)}
            )
        if action == "suggest_tags":
            return JsonResponse({"ok": True, **ai_editor.suggest_tags(source)})
    except PermissionDenied as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except ValidationError as error:
        return JsonResponse({"ok": False, "error": error.messages}, status=400)
    return JsonResponse({"ok": False, "error": "unknown_action"}, status=400)


@login_required
@require_POST
def api_workflow(request: HttpRequest, slug: str) -> JsonResponse:
    """Workflow transitions for a post: request_review, schedule, publish, archive."""
    post = get_object_or_404(Post, slug=slug)
    if not can_manage_post(request.user, post):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    action = request.POST.get("action")
    if not action:
        return JsonResponse({"ok": False, "error": "missing_action"}, status=400)
    if action not in WORKFLOW_ACTIONS:
        return JsonResponse({"ok": False, "error": "invalid_action"}, status=400)
    try:
        when = None
        if action == "schedule" and request.POST.get("when"):
            when = datetime.fromisoformat(request.POST["when"])
        result = run_workflow(post, action, request.user, when)
    except PermissionDenied as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=403)
    except ValidationError as error:
        return JsonResponse({"ok": False, "error": error.messages}, status=400)
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_time"}, status=400)
    log_event(
        logger,
        "info",
        "blog.workflow.transition",
        action=action,
        post=post.slug,
        user=request.user.pk,
        status=result.status,
        correlation_id=getattr(request, "correlation_id", None),
    )
    return JsonResponse(
        {"ok": result.ok, "status": result.status, "message": result.message}
    )


def _live_post_or_404(pk: int) -> Post:
    return get_object_or_404(Post.objects.live(), pk=pk)


@login_required
@require_POST
def post_like(request: HttpRequest, pk: int) -> JsonResponse:
    """Toggle the signed-in user's like on a published post."""
    post = _live_post_or_404(pk)
    with transaction.atomic():
        like, created = PostLike.objects.get_or_create(
            post=post, user_id=request.user.pk
        )
        if not created:
            like.delete()
        Post.objects.filter(pk=post.pk).update(
            likes_count=F("likes_count") + (1 if created else -1)
        )
    post.refresh_from_db(fields=["likes_count"])
    return JsonResponse(
        {
            "ok": True,
            "action": "liked" if created else "unliked",
            "likes_count": max(post.likes_count, 0),
        }
    )


@login_required
@require_POST
def post_bookmark(request: HttpRequest, pk: int) -> JsonResponse:
    """Toggle a session bookmark for a published post."""
    post = _live_post_or_404(pk)
    bookmarks = list(request.session.get(SESSION_BOOKMARKS_KEY, []))
    if post.pk in bookmarks:
        bookmarks.remove(post.pk)
        action = "unbookmarked"
    else:
        bookmarks.append(post.pk)
        action = "bookmarked"
    request.session[SESSION_BOOKMARKS_KEY] = bookmarks
    return JsonResponse(
        {"ok": True, "action": action, "is_bookmarked": post.pk in bookmarks}
    )
