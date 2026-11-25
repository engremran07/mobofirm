"""Member correction proposals stay private until an authorized review."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.firmwares.catalog_update_proposals import review, submit
from apps.firmwares.models import CatalogUpdateProposal


@pytest.mark.django_db
def test_submission_is_verified_idempotent_and_does_not_change_catalog(
    make_user, catalog
):
    contributor = make_user("catalog-correction")
    original = catalog.brand.description

    created = submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "A verified manufacturer description."},
    )
    retry = submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "A verified manufacturer description."},
    )

    catalog.brand.refresh_from_db()
    assert created.created is True
    assert retry.created is False
    assert created.proposal.pk == retry.proposal.pk
    assert created.proposal.status == CatalogUpdateProposal.Status.PENDING
    assert catalog.brand.description == original


@pytest.mark.django_db
def test_unverified_submission_is_rejected(make_user, catalog):
    contributor = make_user("unverified-catalog-correction", verified=False)

    with pytest.raises(PermissionDenied):
        submit(
            contributor,
            target_kind=CatalogUpdateProposal.TargetKind.BRAND,
            target_id=catalog.brand.pk,
            values={"description": "Unverified change"},
        )

    assert not CatalogUpdateProposal.objects.exists()


@pytest.mark.django_db
def test_different_pending_changes_for_same_target_are_refused(make_user, catalog):
    contributor = make_user("catalog-pending-conflict")
    submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "First suggestion."},
    )

    with pytest.raises(ValidationError, match="pending proposal"):
        submit(
            contributor,
            target_kind=CatalogUpdateProposal.TargetKind.BRAND,
            target_id=catalog.brand.pk,
            values={"description": "A competing suggestion."},
        )

    assert CatalogUpdateProposal.objects.count() == 1


@pytest.mark.django_db
def test_reviewer_can_correct_before_approval_and_audit_the_change(make_user, catalog):
    contributor = make_user("catalog-proposal-author")
    reviewer = make_user("catalog-proposal-reviewer", superuser=True)
    proposal = submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "Contributor's suggested text."},
    ).proposal

    result = review(
        proposal.pk,
        reviewer,
        decision="approve",
        reason="Corrected against the manufacturer source.",
        values={"description": "Staff-corrected manufacturer text."},
    )

    catalog.brand.refresh_from_db()
    result.refresh_from_db()
    assert catalog.brand.description == "Staff-corrected manufacturer text."
    assert result.status == CatalogUpdateProposal.Status.APPROVED
    assert result.reviewed_by == reviewer
    assert result.review_reason == "Corrected against the manufacturer source."


@pytest.mark.django_db
def test_rejection_requires_a_reason_and_does_not_change_target(make_user, catalog):
    contributor = make_user("catalog-reject-author")
    reviewer = make_user("catalog-reject-reviewer", superuser=True)
    original = catalog.brand.description
    proposal = submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "Unsupported claim."},
    ).proposal

    with pytest.raises(ValidationError, match="reason"):
        review(proposal.pk, reviewer, decision="reject")

    rejected = review(
        proposal.pk,
        reviewer,
        decision="reject",
        reason="The submitted source could not be verified.",
    )

    catalog.brand.refresh_from_db()
    assert rejected.status == CatalogUpdateProposal.Status.REJECTED
    assert catalog.brand.description == original


@pytest.mark.django_db
def test_stale_proposal_cannot_overwrite_a_newer_catalog_edit(make_user, catalog):
    contributor = make_user("catalog-stale-author")
    reviewer = make_user("catalog-stale-reviewer", superuser=True)
    proposal = submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "Proposal based on old data."},
    ).proposal
    catalog.brand.description = "Newer staff edit."
    catalog.brand.save(update_fields=["description", "updated_at"])

    result = review(proposal.pk, reviewer, decision="approve")

    catalog.brand.refresh_from_db()
    assert result.status == CatalogUpdateProposal.Status.STALE
    assert catalog.brand.description == "Newer staff edit."


@pytest.mark.django_db
def test_public_proposal_form_requires_verification_and_redirects_after_post(
    make_user, catalog
):
    contributor = make_user("catalog-form-author")
    client = Client()
    client.force_login(contributor)
    url = reverse(
        "firmwares:propose_catalog_update",
        kwargs={"target_kind": "brand", "target_id": catalog.brand.pk},
    )

    page = client.get(url)
    response = client.post(url, {"description": "Submitted through the public form."})

    assert page.status_code == 200
    assert b"csrfmiddlewaretoken" in page.content
    assert response.status_code == 302
    assert response["Location"] == reverse("firmwares:my_catalog_update_proposals")
    assert CatalogUpdateProposal.objects.get().submitted_by == contributor


@pytest.mark.django_db
def test_admin_review_route_uses_the_same_approval_service(make_user, catalog):
    contributor = make_user("catalog-admin-author")
    reviewer = make_user("catalog-admin-reviewer", superuser=True)
    proposal = submit(
        contributor,
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        target_id=catalog.brand.pk,
        values={"description": "Proposed description."},
    ).proposal
    client = Client()
    client.force_login(reviewer)
    url = reverse(
        "admin:firmwares_catalogupdateproposal_review",
        args=(proposal.pk,),
    )

    page = client.get(url)
    response = client.post(
        url,
        {
            "description": "Reviewer-corrected description.",
            "decision": "approve",
            "reason": "Confirmed from a reliable source.",
        },
    )

    catalog.brand.refresh_from_db()
    proposal.refresh_from_db()
    assert page.status_code == 200
    assert response.status_code == 302
    assert catalog.brand.description == "Reviewer-corrected description."
    assert proposal.status == CatalogUpdateProposal.Status.APPROVED
