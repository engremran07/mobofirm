"""Editorial workspace: manage, create/edit, history and workflow actions."""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.blog.access import (
    blog_enabled_for,
    can_author_posts,
    can_edit_post,
    can_manage_all_posts,
    can_manage_post,
)
from apps.blog.forms import CategoryForm, PostForm
from apps.blog.models import Post, PostRevision, PostStatus
from apps.blog.services import workflow
from apps.blog.services.history import PostHistoryService
from apps.core.utils.logging import log_event
from apps.tags.tasks import sync_post_tags

logger = logging.getLogger(__name__)

WORKFLOW_ACTIONS = {"request_review", "schedule", "publish", "archive"}


def _require_author(request: HttpRequest) -> None:
    if not blog_enabled_for(request.user) or not can_author_posts(request.user):
        raise Http404


def run_workflow(post: Post, action: str, user, when: datetime | None = None):
    """Single dispatcher shared by the HTML action and the JSON API."""
    if action == "request_review":
        return workflow.request_review(post, user=user)
    if action == "schedule":
        return workflow.schedule(post, when=when or timezone.now(), user=user)
    if action == "publish":
        return workflow.publish(post, user=user)
    if action == "archive":
        return workflow.archive(post, user=user)
    raise ValueError("Unknown workflow action.")


@login_required
def manage_posts(request: HttpRequest) -> HttpResponse:
    _require_author(request)
    can_manage_all = can_manage_all_posts(request.user)
    posts = Post.objects.select_related("author", "category")
    if not can_manage_all:
        posts = posts.filter(author_id=request.user.pk)

    status_filter = request.GET.get("status", "").strip()
    if status_filter in PostStatus.values:
        posts = posts.filter(status=status_filter)
    else:
        status_filter = ""

    source_filter = request.GET.get("source", "").strip()
    if source_filter == "automated":
        posts = posts.filter(is_ai_generated=True)
    elif source_filter == "manual":
        posts = posts.filter(is_ai_generated=False)
    else:
        source_filter = ""

    query = request.GET.get("q", "").strip()[:200]
    if query:
        posts = posts.filter(Q(title__icontains=query) | Q(summary__icontains=query))

    page_obj = Paginator(posts.order_by("-updated_at", "-pk"), 25).get_page(
        request.GET.get("page") or 1
    )
    return render(
        request,
        "blog/manage.html",
        {
            "posts": page_obj,
            "page_obj": page_obj,
            "status_choices": PostStatus.choices,
            "status_filter": status_filter,
            "source_filter": source_filter,
            "query": query,
            "can_manage_all": can_manage_all,
            "can_moderate": workflow.can_moderate(request.user),
        },
    )


@login_required
@require_POST
def post_workflow(request: HttpRequest, slug: str) -> HttpResponse:
    """HTML form action for the workflow buttons; redirects back to the workspace."""
    post = get_object_or_404(Post, slug__iexact=slug)
    if not can_manage_post(request.user, post):
        raise Http404
    action = request.POST.get("action", "")
    if action not in WORKFLOW_ACTIONS:
        messages.error(request, "Unknown action.")
        return redirect("blog:manage_posts")
    when = None
    if action == "schedule":
        try:
            when = datetime.fromisoformat(request.POST.get("when", ""))
        except ValueError:
            messages.error(request, "Choose a valid date and time to schedule.")
            return redirect("blog:manage_posts")
        if timezone.is_naive(when):
            when = timezone.make_aware(when)
    try:
        result = run_workflow(post, action, request.user, when)
    except PermissionDenied:
        messages.error(request, "You do not have permission for that action.")
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, f"{post.title}: {result.message}.")
    return redirect("blog:manage_posts")


@login_required
@require_POST
def bulk_publish(request: HttpRequest) -> HttpResponse:
    """Publish the selected posts; only permitted staff succeed (see the workflow)."""
    ids = request.POST.getlist("post_id")
    if not ids:
        messages.info(request, "No posts selected.")
        return redirect("blog:manage_posts")
    published = rejected = 0
    for post in Post.objects.filter(pk__in=[pk for pk in ids if pk.isdigit()]):
        try:
            workflow.publish(post, user=request.user)
        except (PermissionDenied, ValidationError):
            rejected += 1
        else:
            published += 1
    log_event(
        logger,
        "info",
        "blog.bulk_publish",
        actor=request.user.pk,
        count=published,
        rejected=rejected,
    )
    if rejected:
        messages.warning(
            request,
            f"Published {published} posts; {rejected} were not eligible or lacked permission.",
        )
    else:
        messages.success(request, f"Published {published} posts.")
    return redirect("blog:manage_posts")


@login_required
@require_http_methods(["GET", "POST"])
def post_create(request: HttpRequest) -> HttpResponse:
    _require_author(request)
    instance = None
    edit_slug = request.GET.get("edit") or request.POST.get("edit")
    if edit_slug:
        instance = get_object_or_404(Post, slug__iexact=edit_slug)
        if not can_manage_post(request.user, instance):
            raise Http404
        if not can_edit_post(request.user, instance):
            messages.error(
                request,
                "Only drafts can be edited by their author. Ask an editor to change a live article.",
            )
            return redirect("blog:manage_posts")

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
                    post.featured = False
                    post.save()
                    post.tags.set(tags)
                    PostHistoryService.record_current_version(
                        post, user=request.user, change_summary="Initial version"
                    )
                if not tags:
                    transaction.on_commit(partial(sync_post_tags.delay, post.pk))
            messages.success(request, "Post updated." if instance else "Draft saved.")
            return redirect("blog:post_detail", slug=post.slug)
    else:
        form = PostForm(instance=instance)
    return render(
        request,
        "blog/post_form.html",
        {"form": form, "editing": bool(instance), "editing_post": instance},
    )


@login_required
@require_http_methods(["GET", "POST"])
def category_create(request: HttpRequest) -> HttpResponse:
    _require_author(request)
    if not can_manage_all_posts(request.user):
        raise Http404
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save()
            messages.success(request, f"Category “{category.name}” created.")
            return redirect("blog:manage_posts")
    else:
        form = CategoryForm()
    return render(request, "blog/category_form.html", {"form": form})


@login_required
def post_history(request: HttpRequest, slug: str) -> HttpResponse:
    post = get_object_or_404(Post, slug__iexact=slug)
    if not can_manage_post(request.user, post):
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
    if not can_manage_post(request.user, post):
        raise Http404
    revision = get_object_or_404(PostRevision, pk=revision_id, post=post)
    try:
        PostHistoryService.restore_revision(post, revision, user=request.user)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, "Draft restored from the selected version.")
    return redirect("blog:post_history", slug=post.slug)
