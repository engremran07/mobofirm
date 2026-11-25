"""Member submissions and staff review for updates to published blog posts."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.blog import paywall
from apps.blog.access import blog_enabled_for
from apps.blog.forms import PostUpdateProposalForm, PostUpdateProposalReviewForm
from apps.blog.models import (
    Post,
    PostSource,
    PostUpdateProposal,
    PostUpdateProposalStatus,
)
from apps.blog.services import update_proposals, workflow
from apps.blog.views_contribute import verified_member_required
from apps.core.admin_pages import render_admin_page


def _proposal_post(request: HttpRequest, slug: str) -> Post:
    if not blog_enabled_for(request.user):
        raise Http404("The blog is disabled.")
    post = get_object_or_404(
        Post.objects.filter(status="published")
        .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=timezone.now()))
        .exclude(content_source=PostSource.AUTOMATED)
        .select_related("author", "category")
        .prefetch_related("tags"),
        slug__iexact=slug,
    )
    if paywall.resolve_gate(request.user, post).locked:
        raise Http404("Read access is required to suggest an article update.")
    return post


@verified_member_required
@require_http_methods(["GET", "POST"])
def propose_update(request: HttpRequest, slug: str) -> HttpResponse:
    """Let a verified member propose changes to an eligible published post."""
    post = _proposal_post(request, slug)
    if request.method == "POST":
        form = PostUpdateProposalForm(request.POST, post=post)
        if form.is_valid():
            try:
                result = update_proposals.submit(
                    request.user, post, form.changed_proposal_fields()
                )
            except ValidationError as error:
                form.add_error(None, " ".join(error.messages))
            else:
                message = (
                    "Your update proposal is already waiting for review."
                    if not result.created
                    else "Your update proposal was sent to the blog reviewers."
                )
                messages.success(request, message)
                return redirect("blog:my_update_proposals")
    else:
        form = PostUpdateProposalForm(post=post)
    return render(
        request,
        "blog/update_proposal_form.html",
        {
            "post": post,
            "form": form,
            "proposal_list_url": reverse("blog:my_update_proposals"),
        },
    )


@verified_member_required
@require_GET
def my_update_proposals(request: HttpRequest) -> HttpResponse:
    """List update proposals submitted by the signed-in member."""
    user = request.user
    if not user.is_authenticated:
        raise PermissionDenied("A verified account is required.")
    proposals = (
        PostUpdateProposal.objects.filter(submitted_by=user)
        .select_related("post", "reviewed_by")
        .order_by("-created_at", "-pk")
    )
    page_obj = Paginator(proposals, 25).get_page(request.GET.get("page") or 1)
    return render(
        request,
        "blog/my_update_proposals.html",
        {"proposals": page_obj, "page_obj": page_obj},
    )


def _require_reviewer(request: HttpRequest) -> None:
    if not workflow.can_moderate(request.user):
        raise PermissionDenied("Staff blog-review permission is required.")


@login_required
@require_GET
def update_proposal_queue(request: HttpRequest) -> HttpResponse:
    """List pending update proposals for authorized blog reviewers."""
    _require_reviewer(request)
    pending = (
        PostUpdateProposal.objects.filter(status=PostUpdateProposalStatus.PENDING)
        .select_related("post", "submitted_by")
        .order_by("created_at", "pk")
    )
    page_obj = Paginator(pending, 25).get_page(request.GET.get("page") or 1)
    return render_admin_page(
        request,
        "blog/update_proposal_queue.html",
        title="Post update proposals",
        subtitle=(
            "Review member-suggested changes without editing published content "
            "automatically."
        ),
        breadcrumbs=(
            ("Blog", reverse("blog:manage_posts")),
            ("Update proposals", None),
        ),
        context={"proposals": page_obj, "page_obj": page_obj},
    )


@login_required
@require_http_methods(["GET", "POST"])
def review_update_proposal(request: HttpRequest, proposal_id: int) -> HttpResponse:
    """Review a proposal and apply only an explicit approved decision."""
    _require_reviewer(request)
    proposal = get_object_or_404(
        PostUpdateProposal.objects.select_related(
            "post", "submitted_by", "reviewed_by"
        ),
        pk=proposal_id,
    )
    form = None
    if proposal.status == PostUpdateProposalStatus.PENDING:
        if request.method == "POST":
            form = PostUpdateProposalReviewForm(request.POST, proposal=proposal)
            decision = request.POST.get("decision", "")
            if form.is_valid() and decision not in {"approve", "reject"}:
                form.add_error(None, "Choose approve or reject.")
            elif form.is_valid():
                try:
                    reviewed = update_proposals.review(
                        proposal.pk,
                        request.user,
                        decision=decision,
                        reason=form.cleaned_data["reason"],
                        changes=(
                            form.reviewed_changes() if decision == "approve" else None
                        ),
                    )
                except ValidationError as error:
                    form.add_error(None, " ".join(error.messages))
                else:
                    if reviewed.status == PostUpdateProposalStatus.APPROVED:
                        messages.success(
                            request, "The proposed article update was approved."
                        )
                    elif reviewed.status == PostUpdateProposalStatus.REJECTED:
                        messages.info(
                            request, "The proposal was rejected with a reason."
                        )
                    else:
                        messages.warning(
                            request,
                            "The article changed after this proposal was submitted. "
                            "The proposal was marked stale and was not applied.",
                        )
                    return redirect("blog:update_proposal_queue")
        else:
            form = PostUpdateProposalReviewForm(proposal=proposal)

    return render_admin_page(
        request,
        "blog/update_proposal_review.html",
        title="Review post update proposal",
        breadcrumbs=(
            ("Blog", reverse("blog:manage_posts")),
            ("Update proposals", reverse("blog:update_proposal_queue")),
            ("Review proposal", None),
        ),
        context={"proposal": proposal, "form": form},
    )
