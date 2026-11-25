from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import (
    Post,
    PostRevision,
    PostSource,
    PostStatus,
    PostUpdateProposal,
    PostUpdateProposalStatus,
)
from apps.blog.services import update_proposals
from apps.blog.services.history import PostHistoryService
from apps.tags.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def member(django_user_model):
    return django_user_model.objects.create_user(
        username="update-proposal-member",
        email="update-proposal-member@example.test",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def reviewer(django_user_model):
    user = django_user_model.objects.create_user(
        username="update-proposal-reviewer",
        email="update-proposal-reviewer@example.test",
        is_staff=True,
    )
    user.user_permissions.add(Permission.objects.get(codename="change_post"))
    return user


@pytest.fixture
def published_post(member):
    post = Post.objects.create(
        title="Original guide title",
        summary="Original summary",
        body="<p>Original guide body with enough text to review.</p>",
        author=member,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now(),
    )
    PostHistoryService.record_current_version(
        post, user=member, change_summary="Initial version"
    )
    return post


def test_identical_submission_is_idempotent_and_different_open_change_is_refused(
    member, published_post
):
    changes = {"title": "Suggested guide title"}

    created = update_proposals.submit(member, published_post, changes)
    retry = update_proposals.submit(member, published_post, changes)

    assert created.created
    assert not retry.created
    assert retry.proposal.pk == created.proposal.pk
    with pytest.raises(ValidationError, match="already have an update proposal"):
        update_proposals.submit(
            member, published_post, {"title": "Another suggested title"}
        )
    assert PostUpdateProposal.objects.count() == 1
    published_post.refresh_from_db()
    assert published_post.title == "Original guide title"


def test_unverified_member_and_automated_post_cannot_receive_proposals(
    django_user_model, member, published_post
):
    unverified = django_user_model.objects.create_user(
        username="unverified-proposal-member",
        email="unverified-proposal-member@example.test",
    )
    with pytest.raises(PermissionDenied):
        update_proposals.submit(
            unverified, published_post, {"title": "Suggested title"}
        )

    automated = Post.objects.create(
        title="Automated guide",
        body="<p>Generated and managed by firmware automation.</p>",
        author=member,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now(),
        content_source=PostSource.AUTOMATED,
    )
    with pytest.raises(ValidationError, match="human-authored"):
        update_proposals.submit(member, automated, {"title": "Suggested title"})


def test_staff_can_correct_and_approve_fields_with_revision_history(
    member, reviewer, published_post
):
    tag = Tag.objects.create(
        name="Recovery", slug="recovery", is_curated=True, is_active=True
    )
    proposal = update_proposals.submit(
        member,
        published_post,
        {"title": "Suggested guide title", "tags": [tag.pk]},
    ).proposal

    result = update_proposals.review(
        proposal.pk,
        reviewer,
        decision="approve",
        changes={"title": "Corrected guide title", "tags": [tag.pk]},
        reason="Corrected the title for clarity.",
    )

    published_post.refresh_from_db()
    proposal.refresh_from_db()
    assert result.status == PostUpdateProposalStatus.APPROVED
    assert proposal.reviewed_by == reviewer
    assert proposal.review_reason == "Corrected the title for clarity."
    assert published_post.title == "Corrected guide title"
    assert published_post.version == 2
    assert list(published_post.tags.all()) == [tag]
    revision = PostRevision.objects.get(post=published_post)
    assert revision.snapshot["title"] == "Original guide title"


def test_rejection_requires_a_reason_and_preserves_the_public_post(
    member, reviewer, published_post
):
    proposal = update_proposals.submit(
        member, published_post, {"title": "Suggested guide title"}
    ).proposal

    with pytest.raises(ValidationError, match="reason"):
        update_proposals.review(proposal.pk, reviewer, decision="reject")
    proposal.refresh_from_db()
    assert proposal.status == PostUpdateProposalStatus.PENDING

    result = update_proposals.review(
        proposal.pk,
        reviewer,
        decision="reject",
        reason="This change is not supported by the source.",
    )

    published_post.refresh_from_db()
    assert result.status == PostUpdateProposalStatus.REJECTED
    assert result.review_reason == "This change is not supported by the source."
    assert published_post.title == "Original guide title"
    assert published_post.version == 1


def test_stale_proposal_does_not_overwrite_newer_editorial_content(
    member, reviewer, published_post
):
    proposal = update_proposals.submit(
        member, published_post, {"title": "Member suggestion"}
    ).proposal
    PostHistoryService.update_post(
        published_post,
        user=reviewer,
        change_summary="Staff correction",
        title="Newer staff title",
    )

    result = update_proposals.review(proposal.pk, reviewer, decision="approve")

    published_post.refresh_from_db()
    assert result.status == PostUpdateProposalStatus.STALE
    assert result.reviewed_by == reviewer
    assert published_post.title == "Newer staff title"


def test_only_staff_with_blog_post_permission_can_review(
    member, django_user_model, published_post
):
    staff_without_permission = django_user_model.objects.create_user(
        username="update-proposal-no-permission",
        email="update-proposal-no-permission@example.test",
        is_staff=True,
    )
    proposal = update_proposals.submit(
        member, published_post, {"title": "Suggested title"}
    ).proposal

    with pytest.raises(PermissionDenied):
        update_proposals.review(
            proposal.pk, staff_without_permission, decision="approve"
        )

    proposal.refresh_from_db()
    assert proposal.status == PostUpdateProposalStatus.PENDING


def test_member_form_submits_proposal_without_changing_the_live_article(
    member, published_post
):
    client = Client()
    client.force_login(member)
    url = reverse("blog:propose_update", kwargs={"slug": published_post.slug})

    page = client.get(url)
    response = client.post(
        url,
        {
            "title": "Member suggested title",
            "summary": published_post.summary,
            "body": published_post.body,
            "category": "",
            "tags": [],
        },
    )

    published_post.refresh_from_db()
    proposal = PostUpdateProposal.objects.get()
    assert page.status_code == 200
    assert b"The published article stays unchanged" in page.content
    assert response.status_code == 302
    assert response["Location"] == reverse("blog:my_update_proposals")
    assert proposal.proposed_changes == {"title": "Member suggested title"}
    assert proposal.submitted_by == member
    assert published_post.title == "Original guide title"


def test_public_proposal_form_requires_csrf_and_hides_automated_posts(
    member, published_post
):
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(member)
    url = reverse("blog:propose_update", kwargs={"slug": published_post.slug})
    data = {
        "title": "Member suggested title",
        "summary": published_post.summary,
        "body": published_post.body,
        "category": "",
        "tags": [],
    }

    assert strict.post(url, data).status_code == 403

    automated = Post.objects.create(
        title="Automated article",
        body="<p>Automatically managed firmware content.</p>",
        author=member,
        status=PostStatus.PUBLISHED,
        publish_at=timezone.now(),
        content_source=PostSource.AUTOMATED,
    )
    client = Client()
    client.force_login(member)
    assert (
        client.get(
            reverse("blog:propose_update", kwargs={"slug": automated.slug})
        ).status_code
        == 404
    )


def test_staff_console_reviews_an_edited_proposal_and_requires_post_permission(
    member, reviewer, django_user_model, published_post
):
    proposal = update_proposals.submit(
        member, published_post, {"title": "Member suggested title"}
    ).proposal
    reviewer_client = Client()
    reviewer_client.force_login(reviewer)

    queue = reviewer_client.get(reverse("blog:update_proposal_queue"))
    page = reviewer_client.get(
        reverse("blog:review_update_proposal", kwargs={"proposal_id": proposal.pk})
    )
    response = reviewer_client.post(
        reverse("blog:review_update_proposal", kwargs={"proposal_id": proposal.pk}),
        {
            "decision": "approve",
            "title": "Reviewer corrected title",
            "reason": "Adjusted the wording for accuracy.",
        },
    )
    published_post.refresh_from_db()
    proposal.refresh_from_db()

    assert queue.status_code == page.status_code == 200
    assert b"Review and edit proposed fields" in page.content
    assert response.status_code == 302
    assert response["Location"] == reverse("blog:update_proposal_queue")
    assert published_post.title == "Reviewer corrected title"
    assert proposal.status == PostUpdateProposalStatus.APPROVED
    assert proposal.reviewed_by == reviewer

    staff_without_permission = django_user_model.objects.create_user(
        username="update-proposal-review-page-denied",
        email="update-proposal-review-page-denied@example.test",
        is_staff=True,
    )
    denied = Client()
    denied.force_login(staff_without_permission)
    assert denied.get(reverse("blog:update_proposal_queue")).status_code == 403


def test_member_can_see_only_their_own_proposal_status(member, published_post):
    other = type(member).objects.create_user(
        username="other-update-proposal-member",
        email="other-update-proposal-member@example.test",
        email_verified_at=timezone.now(),
    )
    update_proposals.submit(member, published_post, {"title": "Member title"})
    update_proposals.submit(other, published_post, {"summary": "Other summary"})

    client = Client()
    client.force_login(member)
    response = client.get(reverse("blog:my_update_proposals"))

    assert response.status_code == 200
    assert b"Member title" not in response.content
    assert b"Original guide title" in response.content
    assert b"Other summary" not in response.content
    assert PostUpdateProposal.objects.filter(submitted_by=member).count() == 1
