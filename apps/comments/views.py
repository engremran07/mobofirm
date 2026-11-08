from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.blog.access import can_manage_post, comments_enabled
from apps.blog.models import Post
from apps.comments.models import Comment, CommentReport
from apps.comments.services import (
    edit_comment as edit_comment_service,
)
from apps.comments.services import (
    moderate_comment,
    submit_comment,
    toggle_vote,
)
from apps.comments.services import (
    report_comment as report_comment_service,
)
from apps.core.admin_pages import render_admin_page
from apps.core.utils.ip import get_client_ip
from apps.users.services.rate_limit import allow_action
from apps.users.services.recaptcha import verify_recaptcha

logger = logging.getLogger(__name__)

PER_MINUTE = 5
PER_DAY = 60


def _check_comment_rate_limit(request: HttpRequest) -> bool:
    """Per-account and per-address throttle for every comment write."""
    user_bit = f"user:{request.user.pk}" if request.user.is_authenticated else "anon"
    ip = get_client_ip(request) or "unknown"
    return all(
        (
            allow_action(
                f"comments:rl:min:{user_bit}:{ip}", PER_MINUTE, window_seconds=60
            ),
            allow_action(f"comments:rl:day:{user_bit}", PER_DAY, window_seconds=86400),
        )
    )


def _wants_json(request: HttpRequest) -> bool:
    return request.path.endswith(".json") or "application/json" in request.headers.get(
        "Accept", ""
    )


def _back(request: HttpRequest, post: Post | None, anchor: str = "comments") -> str:
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    if post is not None:
        return f"{post.get_absolute_url()}#{anchor}"
    return "/"


def _respond(
    request: HttpRequest,
    post: Post | None,
    *,
    ok: bool,
    message: str,
    http_status: int = 200,
    anchor: str = "comments",
    **payload,
) -> HttpResponse:
    """JSON for API clients; otherwise a flash message and a redirect (PRG)."""
    if _wants_json(request):
        body = {"ok": ok, "message": message, **payload}
        if not ok:
            body["error"] = message
        return JsonResponse(body, status=http_status)
    (messages.success if ok else messages.error)(request, message)
    return redirect(_back(request, post, anchor))


def _open_post(request: HttpRequest, slug: str) -> Post | HttpResponse:
    """The post a comment is written to, or the response explaining why not."""
    post = get_object_or_404(Post, slug=slug)
    if not comments_enabled():
        return _respond(
            request, post, ok=False, message="Comments are disabled.", http_status=403
        )
    allowed = post.is_live or can_manage_post(request.user, post)
    if not allowed or not post.allow_comments:
        return _respond(
            request,
            post,
            ok=False,
            message="Comments are closed for this article.",
            http_status=403,
        )
    return post


def _check_human(request: HttpRequest) -> str | None:
    """Return an error message when the honeypot, captcha or throttle rejects."""
    if request.POST.get("hp"):
        return "The comment could not be submitted."
    verdict = verify_recaptcha(
        request.POST.get("recaptcha_token", ""),
        get_client_ip(request),
        action="comment",
    )
    if not verdict["ok"]:
        return "Verification failed. Please try again."
    if not _check_comment_rate_limit(request):
        return "You are commenting too quickly. Please wait a moment."
    return None


@login_required
@require_POST
def add_comment(request: HttpRequest, slug: str) -> HttpResponse:
    post = _open_post(request, slug)
    if isinstance(post, HttpResponse):
        return post
    problem = _check_human(request)
    if problem:
        status = 429 if "quickly" in problem else 400
        return _respond(request, post, ok=False, message=problem, http_status=status)
    try:
        result = submit_comment(
            post,
            request.user,
            request.POST.get("body", ""),
            parent_id=request.POST.get("parent"),
            locale=getattr(request, "LANGUAGE_CODE", ""),
        )
    except ValidationError as error:
        return _respond(
            request, post, ok=False, message=" ".join(error.messages), http_status=400
        )
    comment = result.comment
    message = (
        "Your comment is published."
        if result.published
        else "Thanks! Your comment is waiting for review."
    )
    return _respond(
        request,
        post,
        ok=True,
        message=message,
        anchor=f"comment-{comment.pk}" if result.published else "comments",
        id=comment.pk,
        status=comment.status,
        body=comment.body,
        user=comment.user.public_display_name,
        created_at=comment.created_at.isoformat(),
        is_owner=True,
    )


@login_required
@require_POST
def add_comment_json(request: HttpRequest, slug: str) -> HttpResponse:
    return add_comment(request, slug)


@require_GET
def list_comments(request: HttpRequest, slug: str) -> JsonResponse:
    """Published comments as nested JSON, paginated and sortable."""
    post = get_object_or_404(Post, slug=slug)
    if not comments_enabled() or not post.allow_comments:
        return JsonResponse({"error": "comments_disabled"}, status=403)
    if not (post.is_live or can_manage_post(request.user, post)):
        raise Http404
    sort = request.GET.get("sort", "new")
    roots = Comment.objects.filter(
        post=post,
        status=Comment.Status.APPROVED,
        is_deleted=False,
        parent__isnull=True,
    ).select_related("user")
    ordering = {"old": ("created_at",), "top": ("-score", "-created_at")}
    roots = roots.order_by(*ordering.get(sort, ("-created_at",)))
    page_obj = Paginator(roots, 10).get_page(request.GET.get("page") or 1)

    def serialize(comment: Comment, depth: int = 1) -> dict:
        children = []
        if depth < 3:
            children = [
                serialize(child, depth + 1)
                for child in comment.children.filter(
                    status=Comment.Status.APPROVED, is_deleted=False
                )
                .select_related("user")
                .order_by("created_at")
            ]
        return {
            "id": comment.pk,
            "user": comment.user.public_display_name,
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
            "edited_at": comment.edited_at.isoformat() if comment.edited_at else None,
            "score": comment.score,
            "children": children,
            "status": comment.status,
            "is_owner": request.user.is_authenticated
            and request.user.pk == comment.user_id,
        }

    return JsonResponse(
        {
            "items": [serialize(comment) for comment in page_obj],
            "page": page_obj.number,
            "pages": page_obj.paginator.num_pages,
            "total": page_obj.paginator.count,
        }
    )


def _comment_or_404(comment_id: int) -> Comment:
    return get_object_or_404(
        Comment.objects.select_related("post", "user"), pk=comment_id, is_deleted=False
    )


@login_required
@require_POST
def upvote_comment(request: HttpRequest, comment_id: int) -> HttpResponse:
    comment = _comment_or_404(comment_id)
    if not _check_comment_rate_limit(request):
        return _respond(
            request,
            comment.post,
            ok=False,
            message="You are voting too quickly.",
            http_status=429,
        )
    try:
        voted, score = toggle_vote(comment, request.user)
    except ValidationError as error:
        return _respond(
            request,
            comment.post,
            ok=False,
            message=" ".join(error.messages),
            http_status=400,
        )
    return _respond(
        request,
        comment.post,
        ok=True,
        message="Vote added." if voted else "Vote removed.",
        anchor=f"comment-{comment.pk}",
        score=score,
        voted=voted,
    )


@login_required
@require_POST
def report_comment(request: HttpRequest, comment_id: int) -> HttpResponse:
    comment = _comment_or_404(comment_id)
    if not _check_comment_rate_limit(request):
        return _respond(
            request,
            comment.post,
            ok=False,
            message="You are reporting too quickly.",
            http_status=429,
        )
    reason = request.POST.get("reason", CommentReport.Reason.OTHER)
    try:
        created, requeued = report_comment_service(comment, request.user, reason)
    except ValidationError as error:
        return _respond(
            request,
            comment.post,
            ok=False,
            message=" ".join(error.messages),
            http_status=400,
        )
    return _respond(
        request,
        comment.post,
        ok=True,
        message=(
            "Thanks, we will review this comment."
            if created
            else "You have already reported this comment."
        ),
        anchor=f"comment-{comment.pk}",
        reported=created,
        requeued=requeued,
    )


@login_required
@require_POST
def edit_comment(request: HttpRequest, comment_id: int) -> HttpResponse:
    comment = _comment_or_404(comment_id)
    if not _check_comment_rate_limit(request):
        return _respond(
            request,
            comment.post,
            ok=False,
            message="You are editing too quickly.",
            http_status=429,
        )
    try:
        result = edit_comment_service(
            comment, request.user, request.POST.get("body", "")
        )
    except PermissionDenied:
        return _respond(
            request,
            comment.post,
            ok=False,
            message="You can only edit your own comments.",
            http_status=403,
        )
    except ValidationError as error:
        return _respond(
            request,
            comment.post,
            ok=False,
            message=" ".join(error.messages),
            http_status=400,
        )
    return _respond(
        request,
        comment.post,
        ok=True,
        message=(
            "Comment updated."
            if result.published
            else "Comment updated and waiting for review."
        ),
        anchor=f"comment-{comment.pk}",
        id=comment.pk,
        body=result.comment.body,
        edited_at=(
            result.comment.edited_at.isoformat() if result.comment.edited_at else None
        ),
        status=result.comment.status,
    )


@staff_member_required(login_url="admin:login")
def moderation_queue(request: HttpRequest) -> HttpResponse:
    queued = Comment.objects.filter(
        status=Comment.Status.PENDING, is_deleted=False
    ).select_related("user", "post")
    recent = Comment.objects.filter(
        status__in=[
            Comment.Status.APPROVED,
            Comment.Status.REJECTED,
            Comment.Status.SPAM,
        ],
        is_deleted=False,
    ).select_related("user", "post")
    return render_admin_page(
        request,
        "comments/moderation_panel.html",
        title="Comment moderation",
        subtitle=(
            "Clean comments are approved automatically. These need a person: "
            "suspicious wording, reports, edits and anything the screening "
            "could not clear."
        ),
        context={
            "pending_comments": queued.order_by("-toxicity_score", "-created_at")[:50],
            "recent_comments": recent.order_by("-created_at")[:50],
            "can_moderate": request.user.has_perm("comments.change_comment"),
        },
    )


@staff_member_required(login_url="admin:login")
@require_POST
def moderation_action(request: HttpRequest) -> HttpResponse:
    comment = get_object_or_404(Comment, pk=request.POST.get("comment_id"))
    try:
        moderate_comment(
            comment,
            request.POST.get("action", ""),
            request.user,
            note=request.POST.get("note", ""),
        )
    except ValidationError:
        return HttpResponseBadRequest("Unsupported moderation action.")
    except PermissionDenied:
        return HttpResponseForbidden("Comment moderation permission is required.")
    messages.success(request, "Decision recorded.")
    return redirect("comments:moderation_queue")
