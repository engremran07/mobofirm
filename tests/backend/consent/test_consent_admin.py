"""Consent admin: categories, policy versions, audit records and statistics."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.consent import services
from apps.consent.constants import ADVERTISING, ANALYTICS, NECESSARY
from apps.consent.models import ConsentCategory, ConsentDecision, ConsentPolicy

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client() -> Client:
    user = get_user_model().objects.create_superuser(
        username="consent-admin",
        email="consent-admin@example.test",
        password="Pass-12345!",
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def staff_client() -> Client:
    user = get_user_model().objects.create_user(
        username="plain-staff",
        email="plain-staff@example.test",
        password="Pass-12345!",
        is_staff=True,
    )
    client = Client()
    client.force_login(user)
    return client


def test_stats_page_renders_with_data(admin_client):
    services.ensure_defaults()
    admin_client.post(reverse("consent:save"), {"action": "accept_all"})
    response = admin_client.get(reverse("admin:consent_consentdecision_stats"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Opt-in rate by category" in body
    assert "Analytics" in body


def test_stats_page_requires_permission(staff_client):
    response = staff_client.get(reverse("admin:consent_consentdecision_stats"))
    assert response.status_code in (302, 403)


def test_records_are_read_only(admin_client):
    policy = services.ensure_defaults()
    record = ConsentDecision.objects.create(
        subject="00000000-0000-0000-0000-000000000001",
        policy=policy,
        action="reject_all",
        source="banner",
    )
    assert (
        admin_client.get(reverse("admin:consent_consentdecision_add")).status_code
        == 403
    )
    delete = admin_client.post(
        reverse("admin:consent_consentdecision_delete", args=[record.pk]),
        {"post": "yes"},
    )
    assert delete.status_code == 403
    assert ConsentDecision.objects.filter(pk=record.pk).exists()


def test_builtin_categories_cannot_be_deleted(admin_client):
    services.ensure_defaults()
    category = ConsentCategory.objects.get(slug=ANALYTICS)
    response = admin_client.post(
        reverse("admin:consent_consentcategory_delete", args=[category.pk]),
        {"post": "yes"},
    )
    assert response.status_code == 403
    assert ConsentCategory.objects.filter(slug=ANALYTICS).exists()
    custom = ConsentCategory.objects.create(
        slug="social", name="Social", description="x"
    )
    response = admin_client.post(
        reverse("admin:consent_consentcategory_delete", args=[custom.pk]),
        {"post": "yes"},
    )
    assert response.status_code == 302
    assert not ConsentCategory.objects.filter(slug="social").exists()
    assert (
        ConsentCategory.objects.filter(slug__in=[NECESSARY, ADVERTISING]).count() == 2
    )


def test_new_policy_version_is_numbered_automatically_and_published_by_action(
    admin_client,
):
    services.ensure_defaults()
    response = admin_client.post(
        reverse("admin:consent_consentpolicy_add"),
        {
            "title": "Updated policy",
            "banner_text": "We changed our trackers.",
            "details_text": "Details",
            "privacy_url": "/pages/privacy/",
        },
    )
    assert response.status_code == 302
    draft = ConsentPolicy.objects.get(title="Updated policy")
    assert draft.version == 2 and not draft.is_published
    assert services.active_policy().version == 1

    change = admin_client.get(
        reverse("admin:consent_consentpolicy_change", args=[draft.pk])
    )
    assert change.status_code == 200
    services.publish_policy(draft)
    assert services.active_policy().version == 2

    locked = admin_client.get(
        reverse("admin:consent_consentpolicy_change", args=[draft.pk])
    )
    assert b'name="banner_text"' not in locked.content


def test_published_policy_cannot_be_deleted(admin_client):
    policy = services.ensure_defaults()
    response = admin_client.post(
        reverse("admin:consent_consentpolicy_delete", args=[policy.pk]), {"post": "yes"}
    )
    assert response.status_code == 403
    assert ConsentPolicy.objects.filter(pk=policy.pk).exists()
