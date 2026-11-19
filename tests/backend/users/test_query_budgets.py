"""Contribution and library pages cost the same number of queries however many rows."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.firmwares.models import (
    Brand,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from apps.users import library

pytestmark = pytest.mark.django_db


@pytest.fixture(name="member")
def member_fixture(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(
        username="budget_member",
        email="budget-member@example.com",
        password="Pass-12345!",
        email_verified_at=timezone.now(),
    )


def _approved_submissions(member: Any, count: int, start: int = 0) -> None:
    brand, _ = Brand.objects.get_or_create(slug="acme", defaults={"name": "Acme"})
    model, _ = Model.objects.get_or_create(
        brand=brand, slug="phone", defaults={"name": "Phone"}
    )
    variant, _ = Variant.objects.get_or_create(
        model=model, slug="global", defaults={"name": "Global"}
    )
    for number in range(start, start + count):
        published = OfficialFirmware.objects.create(
            original_file_name=f"live-{number}.zip",
            stored_file_path=f"/firmware/live-{number}.zip",
            brand=brand,
            model=model,
            variant=variant,
            is_verified=True,
            is_active=True,
        )
        PendingFirmware.objects.create(
            original_file_name=f"live-{number}.zip",
            stored_file_path=f"/firmware/live-{number}.zip",
            uploader=member,
            uploaded_brand=brand,
            uploaded_model=model,
            uploaded_variant=variant,
            admin_decision="approved",
            routed_category="official",
            routed_object_id=published.pk,
        )


def _queries_for_uploads(member: Any) -> tuple[int, list[library.UploadEntry]]:
    with CaptureQueriesContext(connection) as captured:
        entries = library.upload_entries(member)
    return len(captured), entries


def test_upload_entries_do_not_query_once_per_approved_row(member):
    _approved_submissions(member, 2)
    few_queries, few = _queries_for_uploads(member)
    _approved_submissions(member, 8, start=2)

    many_queries, many = _queries_for_uploads(member)

    assert len(few) == 2 and len(many) == 10
    assert many_queries == few_queries
    assert all(entry.published_url for entry in many)


def test_a_withdrawn_firmware_has_no_published_link(member):
    _approved_submissions(member, 2)
    OfficialFirmware.objects.filter(original_file_name="live-0.zip").update(
        is_active=False
    )

    entries = {entry.name: entry for entry in library.upload_entries(member)}

    assert entries["live-0.zip"].published_url == ""
    assert entries["live-1.zip"].published_url.endswith("/")
