from __future__ import annotations

from datetime import timedelta

import requests
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.blog.models import Post
from apps.comments.services.moderation import classify_comment
from apps.core.app_service import AppService
from apps.core.utils.ip import get_client_ip
from apps.core.views import _get_site_settings_snapshot
from apps.users.services.decorators import consent_required
from apps.users.services.rate_limit import allow_action
from apps.governance.services import submit_for_review

from .models import Comment


@login_required
@require_POST
@consent_required(["functional", "comments"])
def add_comment(request: HttpRequest, slug: str) -> HttpResponse:
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
    try:
        comment_api = AppService.get("comments")
        comment_settings = (
            comment_api.get_settings()
            if comment_api and hasattr(comment_api, "get_settings")
            else {}
        )
    except Exception:
        comment_api = None
        comment_settings = {}
    if not comment_settings.get(
        "enable_comments", False if comment_api is None else True
    ):
        return HttpResponseBadRequest("Comments are disabled.")
    if not blog_settings.get(
        "enable_blog", settings_snapshot.get("enable_blog")
    ) or not blog_settings.get(
        "enable_blog_comments", settings_snapshot.get("enable_blog_comments")
    ):
        return HttpResponseBadRequest("Comments are disabled.")
    if not _has_comments_consent(request):
        return HttpResponseForbidden("Consent required.")

    post = get_object_or_404(Post, slug=slug)
    if hasattr(post, "allow_comments") and not post.allow_comments:
        return HttpResponseBadRequest("Comments are disabled for this post.")
    if not post.is_live and not (
        getattr(request.user, "is_staff", False) or request.user == post.author
    ):
        return HttpResponseBadRequest("Comments are disabled for this post.")
    body = (request.POST.get("body") or "").strip()
    if request.POST.get("hp"):
        return HttpResponseBadRequest("Comment body required.")
    if not body:
        return HttpResponseBadRequest("Comment body required.")
    if not _verify_recaptcha(request):
        return HttpResponseBadRequest("Recaptcha failed.")
    if not _check_comment_rate_limit(request):
        return HttpResponseBadRequest("Too many comments, slow down.")
    with transaction.atomic():
        comment = Comment.objects.create(
            post=post,
            user=request.user,
            body=body,
            status=Comment.Status.PENDING,
            is_approved=False,
        )
        submit_for_review(comment, actor=request.user)
    return redirect("blog:post_detail", slug=slug)


@require_GET
def list_comments(request: HttpRequest, slug: str) -> JsonResponse:
    """
    JSON API for comments with pagination and sorting.
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
    try:
        comment_api = AppService.get("comments")
        comment_settings = (
            comment_api.get_settings()
            if comment_api and hasattr(comment_api, "get_settings")
            else {}
        )
    except Exception:
        comment_api = None
        comment_settings = {}
    if not comment_settings.get(
        "enable_comments", False if comment_api is None else True
    ):
        return JsonResponse({"error": "comments_disabled"}, status=403)
    if not blog_settings.get(
        "enable_blog", settings_snapshot.get("enable_blog")
    ) or not blog_settings.get(
        "enable_blog_comments", settings_snapshot.get("enable_blog_comments")
    ):
        return JsonResponse({"error": "comments_disabled"}, status=403)
    if not _has_comments_consent(request):
        return JsonResponse({"error": "consent_required"}, status=403)

    post = get_object_or_404(Post, slug=slug)
    if hasattr(post, "allow_comments") and not post.allow_comments:
        return JsonResponse({"error": "comments_disabled"}, status=403)
    if not post.is_live and not (
        getattr(request.user, "is_staff", False) or request.user == post.author
    ):
        return JsonResponse({"error": "comments_disabled"}, status=403)
    sort = request.GET.get("sort", "new")
    qs = Comment.objects.filter(
        post=post,
        status=Comment.Status.APPROVED,
        is_deleted=False,
        parent__isnull=True,
    ).prefetch_related(
        "children",
        "children__user",  # Fix N+1 query in recursive serialize()
    )
    if sort == "old":
        qs = qs.order_by("created_at")
    elif sort == "top":
        qs = qs.order_by("-score", "-created_at")
    else:
        qs = qs.order_by("-created_at")
    paginator = Paginator(qs, 10)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    def serialize(comment):
        children = [
            serialize(child)
            for child in comment.children.all()
            if child.status == Comment.Status.APPROVED and not child.is_deleted
        ]
        return {
            "id": comment.pk,
            "user": str(comment.user),
            "user_id": comment.user_id,  # type: ignore[attr-defined]
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
            "edited_at": comment.edited_at.isoformat() if comment.edited_at else None,
            "score": comment.score,
            "children": children,
            "metadata": comment.metadata or {},
            "status": comment.status,
            "is_owner": request.user.is_authenticated
            and request.user.pk == comment.user_id,  # type: ignore[attr-defined]
        }

    payload = [serialize(c) for c in page_obj]
    return JsonResponse(
        {
            "items": payload,
            "page": page_obj.number,
            "pages": paginator.num_pages,
            "total": paginator.count,
            "recaptcha_required": bool(getattr(settings, "RECAPTCHA_SECRET_KEY", None)),
            "recaptcha_site_key": getattr(settings, "RECAPTCHA_PUBLIC_KEY", ""),
        }
    )


@login_required
@require_POST
@consent_required(["functional", "comments"])
def add_comment_json(request: HttpRequest, slug: str) -> JsonResponse:
    """
    JSON POST endpoint for async comment submission.
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
    try:
        comment_api = AppService.get("comments")
        comment_settings = (
            comment_api.get_settings()
            if comment_api and hasattr(comment_api, "get_settings")
            else {}
        )
    except Exception:
        comment_api = None
        comment_settings = {}
    if not comment_settings.get(
        "enable_comments", False if comment_api is None else True
    ):
        return JsonResponse({"error": "Comments are disabled."}, status=400)
    if not blog_settings.get(
        "enable_blog", settings_snapshot.get("enable_blog")
    ) or not blog_settings.get(
        "enable_blog_comments", settings_snapshot.get("enable_blog_comments")
    ):
        return JsonResponse({"error": "Comments are disabled."}, status=400)
    if not _has_comments_consent(request):
        return JsonResponse({"error": "consent_required"}, status=403)
    if not _check_comment_rate_limit(request):
        return JsonResponse({"error": "rate_limited"}, status=429)
    post = get_object_or_404(Post, slug=slug)
    if hasattr(post, "allow_comments") and not post.allow_comments:
        return JsonResponse({"error": "comments_disabled"}, status=403)
    if not post.is_live and not (
        getattr(request.user, "is_staff", False) or request.user == post.author
    ):
        return JsonResponse({"error": "comments_disabled"}, status=403)
    body = (request.POST.get("body") or "").strip()
    if request.POST.get("hp"):
        return JsonResponse({"error": "Comment body required."}, status=400)
    if not body:
        return JsonResponse({"error": "Comment body required."}, status=400)
    if not _verify_recaptcha(request):
        return JsonResponse({"error": "recaptcha_failed"}, status=400)
    parent_id = request.POST.get("parent")
    parent = None
    if parent_id:
        parent = Comment.objects.filter(pk=parent_id, post=post).first()
    meta = {}
    status = Comment.Status.PENDING
    toxicity_score = 0.0
    # Basic language detection flag for auditing
    meta["locale"] = getattr(request, "locale", None) or request.LANGUAGE_CODE
    try:
        moderation = classify_comment(body, context=post.title)
        meta["moderation"] = {
            "label": moderation.label,
            "score": moderation.score,
            "rationale": moderation.rationale,
        }
        toxicity_score = float(moderation.score or 0.0)
        label = str(moderation.label or "pending").lower()
        if label == "spam":
            status = Comment.Status.SPAM
        elif label == "rejected":
            status = Comment.Status.REJECTED
        if label in {"spam", "abuse"} or toxicity_score >= 0.5:
            meta["requires_priority_review"] = True
    except Exception:
        meta["moderation_unavailable"] = True
    # Akismet spam check (optional)
    try:
        if _check_akismet(request, body, post):
            meta["akismet"] = "spam"
    except Exception:  # noqa: S110
        pass
    with transaction.atomic():
        comment = Comment.objects.create(
            post=post,
            user=request.user,
            body=body,
            parent=parent,
            created_at=timezone.now(),
            is_approved=False,
            status=status,
            metadata=meta,
            moderation_flags=meta,
            toxicity_score=toxicity_score,
        )
        submit_for_review(comment, actor=request.user)
    status = comment.status
    msg = "Submitted for human review"
    return JsonResponse(
        {
            "ok": True,
            "id": comment.pk,
            "body": comment.body,
            "user": str(comment.user),
            "created_at": comment.created_at.isoformat(),
            "metadata": meta,
            "status": status,
            "message": msg,
            "is_owner": True,
        }
    )


@login_required
@require_POST
@consent_required(["functional", "comments"])
def upvote_comment(request: HttpRequest, comment_id: int) -> JsonResponse:
    if not _has_comments_consent(request):
        return JsonResponse({"error": "consent_required"}, status=403)
    if not _check_comment_rate_limit(request):
        return JsonResponse({"error": "rate_limited"}, status=429)
    comment = get_object_or_404(Comment, pk=comment_id)
    Comment.objects.filter(pk=comment.pk).update(score=F("score") + 1)
    comment.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "score": comment.score,
            "status": comment.status,
            "is_deleted": comment.is_deleted,
        }
    )


@login_required
@require_POST
@consent_required(["functional", "comments"])
def report_comment(request: HttpRequest, comment_id: int) -> JsonResponse:
    """
    Lightweight reporting endpoint to flag a comment for moderation.
    """
    if not _has_comments_consent(request):
        return JsonResponse({"error": "consent_required"}, status=403)
    comment = get_object_or_404(Comment, pk=comment_id, is_deleted=False)
    meta = comment.moderation_metadata or {}  # type: ignore[attr-defined]
    reports = meta.get("reports", 0)
    meta["reports"] = reports + 1
    comment.moderation_metadata = meta  # type: ignore[attr-defined]
    comment.status = Comment.Status.PENDING
    comment.save(update_fields=["moderation_metadata", "status"])
    return JsonResponse({"ok": True, "status": comment.status})


@login_required
@require_POST
@consent_required(["functional", "comments"])
def edit_comment(request: HttpRequest, comment_id: int) -> JsonResponse:
    """
    Allow owners to edit their comment within a grace period; keep history.
    """
    if not _has_comments_consent(request):
        return JsonResponse({"error": "consent_required"}, status=403)

    comment = get_object_or_404(Comment, pk=comment_id, is_deleted=False)
    if comment.user_id != request.user.pk and not getattr(  # type: ignore[attr-defined]
        request.user, "is_staff", False
    ):
        return JsonResponse({"error": "forbidden"}, status=403)

    # Grace window: 24 hours after creation unless staff
    if not getattr(
        request.user, "is_staff", False
    ) and comment.created_at < timezone.now() - timedelta(hours=24):
        return JsonResponse({"error": "edit_window_expired"}, status=400)

    body = (request.POST.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "Comment body required."}, status=400)

    history = comment.metadata.get("history", []) if comment.metadata else []
    history.append(
        {
            "body": comment.body,
            "edited_at": timezone.now().isoformat(),
        }
    )
    # Keep last 5 history entries
    history = history[-5:]

    comment.body = body
    comment.edited_at = timezone.now()
    comment.metadata = comment.metadata or {}
    comment.metadata["history"] = history
    comment.save(update_fields=["body", "edited_at", "metadata"])

    return JsonResponse(
        {
            "ok": True,
            "id": comment.pk,
            "body": comment.body,
            "edited_at": comment.edited_at.isoformat(),
        }
    )


def _check_comment_rate_limit(request: HttpRequest) -> bool:
    """
    Simple per-IP + user throttle to prevent abuse.
    """
    key_bits = []
    if getattr(request, "user", None) and request.user.is_authenticated:
        key_bits.append(f"user:{request.user.pk}")
    ip = get_client_ip(request) or "anon"
    key_bits.append(f"ip:{ip}")
    key = "comments:rl:" + ":".join(key_bits)
    return allow_action(key, max_attempts=10, window_seconds=60)


def _has_comments_consent(request: HttpRequest) -> bool:
    """
    Returns True only when functional consent is granted and comment consent
    (if defined) is accepted. Unknown or missing consent defaults to deny.
    """
    consent = getattr(request, "consent_categories", None)
    if not consent:
        return False
    if not consent.get("functional", False):
        return False
    if "comments" in consent and not consent.get("comments", False):
        return False
    return True


def _verify_recaptcha(request: HttpRequest) -> bool:
    """
    Optional Recaptcha verification. Returns True if verification is disabled or passes.
    Expects token in request.POST['recaptcha_token'] when enabled.
    """
    secret = getattr(settings, "RECAPTCHA_PRIVATE_KEY", None)
    if not secret:
        return True
    token = request.POST.get("recaptcha_token") or ""
    if not token:
        return False
    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": token},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("success"))
    except Exception:
        return False


def _check_akismet(request: HttpRequest, body: str, post: Post) -> bool:
    """
    Optional Akismet spam check. Returns True if flagged as spam.
    """
    api_key = getattr(settings, "AKISMET_API_KEY", None)
    blog_url = getattr(settings, "AKISMET_BLOG_URL", None) or getattr(
        settings, "SITE_URL", None
    )
    if not api_key or not blog_url:
        return False
    try:
        ip = get_client_ip(request) or ""
        useragent = request.META.get("HTTP_USER_AGENT", "")
        referrer = request.META.get("HTTP_REFERER", "")
        data = {
            "blog": blog_url,
            "user_ip": ip,
            "user_agent": useragent,
            "referrer": referrer,
            "comment_type": "comment",
            "comment_author": str(getattr(request.user, "username", "")),
            "comment_content": body,
            "permalink": request.build_absolute_uri(post.get_absolute_url()),
        }
        resp = requests.post(
            f"https://{api_key}.rest.akismet.com/1.1/comment-check",
            data=data,
            timeout=5,
        )
        resp.raise_for_status()
        result = resp.text.strip().lower()
        return result == "true"
    except Exception:
        return False


def _notify_post_author(comment: Comment, request: HttpRequest) -> None:
    """
    Notify post author on new comment (best-effort).
    """
    try:
        post = comment.post
        author = getattr(post, "author", None)
        if not (post and author and author.email):
            return
        subject = f"New comment on: {post.title}"
        body = (
            f"A new comment was posted by {comment.user}.\n\n"
            f"Content:\n{comment.body}\n\n"
            f"Status: {comment.status}\n"
            f"Link: {request.build_absolute_uri(post.get_absolute_url())}"
        )
        send_mail(
            subject,
            body,
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            [author.email],
            fail_silently=True,
        )
    except Exception:
        return


def _notify_parent_commenter(
    parent: Comment | None, comment: Comment, request: HttpRequest
) -> None:
    """
    Notify parent commenter on reply (best-effort).
    """
    if not parent:
        return
    try:
        if not getattr(parent, "user", None) or not parent.user.email:
            return
        subject = f"New reply to your comment on: {comment.post.title}"
        body = (
            f"{comment.user} replied to your comment.\n\n"
            f"Reply:\n{comment.body}\n\n"
            f"Status: {comment.status}\n"
            f"Link: {request.build_absolute_uri(comment.post.get_absolute_url())}"
        )
        send_mail(
            subject,
            body,
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
            [parent.user.email],
            fail_silently=True,
        )
    except Exception:
        return


@staff_member_required(login_url="admin:login")
def moderation_queue(request: HttpRequest) -> HttpResponse:
    pending = Comment.objects.filter(
        status=Comment.Status.PENDING, is_deleted=False
    ).order_by("-created_at")[:50]
    recent = Comment.objects.filter(
        status__in=[
            Comment.Status.APPROVED,
            Comment.Status.REJECTED,
            Comment.Status.SPAM,
        ],
        is_deleted=False,
    ).order_by("-created_at")[:50]
    return render(
        request,
        "comments/moderation_panel.html",
        {"pending_comments": pending, "recent_comments": recent},
    )


@staff_member_required(login_url="admin:login")
@require_POST
def moderation_action(request: HttpRequest) -> HttpResponse:
    cid = request.POST.get("comment_id")
    action = request.POST.get("action")
    comment = get_object_or_404(Comment, pk=cid)
    if action == "approve":
        comment.status = Comment.Status.APPROVED
        comment.is_approved = True
    elif action == "reject":
        comment.status = Comment.Status.REJECTED
        comment.is_approved = False
    elif action == "spam":
        comment.status = Comment.Status.SPAM
        comment.is_approved = False
    comment.save(update_fields=["status", "is_approved"])
    return redirect("comments:moderation_queue")
