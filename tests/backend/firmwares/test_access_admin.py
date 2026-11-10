"""Admin screens for access changes: dialog, bulk action, request queue, history."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse

from apps.firmwares import access_changes, access_requests
from apps.firmwares.access_change_models import (
    AccessChangeLog,
    AccessChangeRequest,
    AutoFeatureSettings,
)
from apps.firmwares.models import (
    Brand,
    Model,
    OfficialFirmware,
    ServedDownload,
    Variant,
)

pytestmark = pytest.mark.django_db
DIALOG = {"_form_submitted": "on"}


@pytest.fixture(name="staff_client")
def staff_client_fixture(django_user_model) -> Client:
    staff = django_user_model.objects.create_superuser(
        username="access-admin", email="access-admin@example.test"
    )
    client = Client()
    client.force_login(staff)
    return client


@pytest.fixture(name="firmware")
def firmware_fixture(django_user_model) -> OfficialFirmware:
    uploader = django_user_model.objects.create_user(
        username="uploader", email="uploader@example.test", is_staff=True
    )
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone", slug="phone")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    return OfficialFirmware.objects.create(
        original_file_name="fw.zip",
        stored_file_path="/firmware/fw.zip",
        brand=brand,
        model=model,
        variant=variant,
        is_verified=True,
        uploader=uploader,
    )


def action_url(name: str, pk: Any) -> str:
    return reverse(f"admin:firmwares_officialfirmware_{name}", args=[pk])


def test_the_dialog_changes_the_access_type_and_records_it(
    staff_client, firmware
) -> None:
    opened = staff_client.get(action_url("change_access", firmware.pk))
    assert opened.status_code == 200
    assert b"New access type" in opened.content

    done = staff_client.post(
        action_url("change_access", firmware.pk),
        {
            **DIALOG,
            "access_tier": "paid",
            "approved_price": "5.25",
            "reason": "Rare build",
        },
    )

    assert "HX-Redirect" in done.headers
    firmware.refresh_from_db()
    assert (firmware.access_tier, firmware.approved_price) == ("paid", Decimal("5.25"))
    log = AccessChangeLog.objects.get()
    assert (log.source, log.reason) == ("dialog", "Rare build")


def test_the_dialog_validates_the_price(staff_client, firmware) -> None:
    done = staff_client.post(
        action_url("change_access", firmware.pk),
        {**DIALOG, "access_tier": "paid", "reason": "Rare build"},
    )

    assert "HX-Redirect" not in done.headers
    firmware.refresh_from_db()
    assert firmware.access_tier == "free"
    assert AccessChangeLog.objects.count() == 0


def test_the_form_no_longer_edits_the_access_type(staff_client, firmware) -> None:
    response = staff_client.get(
        reverse("admin:firmwares_officialfirmware_change", args=[firmware.pk])
    )

    assert response.status_code == 200
    assert b'name="access_tier"' not in response.content
    assert b"No change since approval." in response.content


def test_the_bulk_action_changes_every_selected_file(staff_client, firmware) -> None:
    response = staff_client.post(
        reverse("admin:firmwares_officialfirmware_changelist"),
        {
            "action": "change_access_selected",
            "index": "0",
            "_selected_action": [str(firmware.pk)],
            "access_tier": "featured",
            "reason": "Bulk promotion",
        },
        follow=True,
    )

    assert response.status_code == 200
    firmware.refresh_from_db()
    firmware.refresh_from_db()
    assert firmware.access_tier == "featured"
    assert AccessChangeLog.objects.get().source == "bulk"


def test_the_bulk_action_needs_a_type(staff_client, firmware) -> None:
    staff_client.post(
        reverse("admin:firmwares_officialfirmware_changelist"),
        {
            "action": "change_access_selected",
            "index": "0",
            "_selected_action": [str(firmware.pk)],
        },
        follow=True,
    )

    firmware.refresh_from_db()
    assert firmware.access_tier == "free"


def test_the_request_queue_approves_and_rejects(
    staff_client, firmware, monkeypatch
) -> None:
    monkeypatch.setattr(access_requests, "is_seller", lambda user: True)
    request = access_requests.submit_request(
        firmware,
        firmware.uploader,
        to_tier="paid",
        proposed_price="8",
        reason="Please sell this build",
    )
    assert (
        staff_client.get(
            reverse("admin:firmwares_accesschangerequest_changelist")
        ).status_code
        == 200
    )
    assert (
        b"Please sell"
        in staff_client.get(
            reverse("admin:firmwares_accesschangerequest_change", args=[request.pk])
        ).content
    )

    approved = staff_client.post(
        reverse(
            "admin:firmwares_accesschangerequest_approve_request", args=[request.pk]
        ),
        {**DIALOG, "approved_price": "6.5", "note": "OK"},
    )

    assert "HX-Redirect" in approved.headers
    firmware.refresh_from_db()
    request.refresh_from_db()
    assert (firmware.access_tier, firmware.approved_price) == ("paid", Decimal("6.50"))
    assert request.status == AccessChangeRequest.Status.APPROVED

    second = access_requests.submit_request(
        firmware,
        firmware.uploader,
        to_tier="featured",
        reason="Move it to members",
    )
    missing = staff_client.post(
        reverse("admin:firmwares_accesschangerequest_reject_request", args=[second.pk]),
        DIALOG,
    )
    assert "HX-Redirect" not in missing.headers
    rejected = staff_client.post(
        reverse("admin:firmwares_accesschangerequest_reject_request", args=[second.pk]),
        {**DIALOG, "reason": "Stay paid"},
    )
    assert "HX-Redirect" in rejected.headers
    second.refresh_from_db()
    assert second.status == AccessChangeRequest.Status.REJECTED


def test_the_history_is_read_only_and_a_change_can_be_reverted(
    staff_client, firmware, django_user_model
) -> None:
    other = django_user_model.objects.create_user(
        username="mod", email="mod@example.test", is_staff=True
    )
    log = access_changes.apply_access_change(
        firmware,
        access_changes.AccessChange(to_tier="featured", actor=other, reason="x"),
    )
    assert (
        staff_client.get(
            reverse("admin:firmwares_accesschangelog_changelist")
        ).status_code
        == 200
    )
    assert (
        staff_client.get(
            reverse("admin:firmwares_accesschangelog_delete", args=[log.pk])
        ).status_code
        == 403
    )

    done = staff_client.post(
        reverse("admin:firmwares_accesschangelog_revert_latest", args=[log.pk]),
        {**DIALOG, "reason": "Undo"},
    )

    assert "HX-Redirect" in done.headers
    firmware.refresh_from_db()
    assert firmware.access_tier == "free"
    assert AccessChangeLog.objects.count() == 2


def test_the_auto_feature_settings_page_renders(staff_client) -> None:
    AutoFeatureSettings.get_solo()

    response = staff_client.get(reverse("admin:firmwares_autofeaturesettings_change"))

    assert response.status_code == 200
    assert b"No file meets every threshold." in response.content


def test_the_served_download_admin_resets_a_free_allowance(
    staff_client, firmware, django_user_model
) -> None:
    member = django_user_model.objects.create_user(
        username="member", email="member@example.test"
    )
    row = ServedDownload.objects.create(
        user=member,
        content_type=ContentType.objects.get_for_model(
            firmware, for_concrete_model=False
        ),
        object_id=firmware.pk,
        tier="free",
        times_served=9,
    )
    changelist = reverse("admin:firmwares_serveddownload_changelist")
    assert staff_client.get(changelist).status_code == 200

    done = staff_client.post(
        reverse("admin:firmwares_serveddownload_reset_allowance", args=[row.pk]),
        {**DIALOG, "reason": "Support ticket"},
    )

    assert "HX-Redirect" in done.headers
    row.refresh_from_db()
    assert row.times_served == 1


def test_the_shop_settings_expose_the_download_rules(staff_client) -> None:
    response = staff_client.get(reverse("admin:shop_shopsettings_change"))

    assert response.status_code == 200
    assert b"free_redownload_window_days" in response.content
    assert b"package_history_retention_days" in response.content
