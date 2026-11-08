"""Firmware approval: the ``firmware.approved`` event and the contributor rewards."""

from __future__ import annotations

import tempfile
import types
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.firmwares import reward_events
from apps.firmwares.events import CONTRIBUTION_APPROVED, FIRMWARE_APPROVED
from apps.firmwares.models import Brand, Model, Variant
from apps.firmwares.services import approve_pending_firmware, handle_upload
from apps.firmwares.tracking_models import FirmwareRequest
from apps.wallet.models import LedgerEntry
from apps.wallet.registry import reward_events as registered_events


@pytest.fixture(autouse=True)
def stage(settings):
    with tempfile.TemporaryDirectory() as directory:
        settings.FIRMWARE_STORAGE_ROOT = Path(directory)
        yield


@pytest.fixture
def make_user(db):
    def factory(username, *, staff=False, superuser=False):
        manager = get_user_model().objects
        values = {
            "username": username,
            "email": f"{username}@example.test",
            "email_verified_at": timezone.now(),
        }
        if superuser:
            return manager.create_superuser(**values)
        return manager.create_user(is_staff=staff, **values)

    return factory


@pytest.fixture
def catalog(db):
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    return types.SimpleNamespace(brand=brand, model=model, variant=variant)


@pytest.fixture
def published():
    seen: list[dict] = []
    event_bus.subscribe(FIRMWARE_APPROVED, seen.append)
    yield seen
    event_bus.unsubscribe(FIRMWARE_APPROVED, seen.append)


@pytest.fixture
def contributions():
    seen: list[dict] = []
    event_bus.subscribe(CONTRIBUTION_APPROVED, seen.append)
    yield seen
    event_bus.unsubscribe(CONTRIBUTION_APPROVED, seen.append)


def pending_upload(catalog, uploader, name="reward.zip"):
    return handle_upload(
        uploader=uploader,
        uploaded_brand=catalog.brand,
        uploaded_model=catalog.model,
        uploaded_variant=catalog.variant,
        file_obj=SimpleUploadedFile(name, b"bytes", content_type="application/zip"),
        is_password_protected=False,
        password="",
    )


def approve(pending, reviewer, capture, **kwargs):
    with capture(execute=True):
        return approve_pending_firmware(
            pending, reviewer, category="official", **kwargs
        )


@pytest.mark.django_db
class TestApprovalRewards:
    def test_the_events_are_registered_with_tiered_defaults(self):
        events = registered_events()

        assert events[reward_events.APPROVED_EVENT].default_credits == 10
        assert (
            events[reward_events.APPROVED_FEATURED_EVENT].default_credits
            > events[reward_events.APPROVED_PAID_EVENT].default_credits
            > events[reward_events.APPROVED_EVENT].default_credits
        )
        assert reward_events.REQUEST_FULFILLED_EVENT in events

    def test_the_uploader_earns_credits_once_per_approved_upload(
        self, make_user, catalog, django_capture_on_commit_callbacks
    ):
        uploader, reviewer = make_user("earner"), make_user("approver", staff=True)
        reviewer.is_superuser = True
        reviewer.save()
        pending = pending_upload(catalog, uploader)

        firmware = approve(pending, reviewer, django_capture_on_commit_callbacks)
        approve(pending, reviewer, django_capture_on_commit_callbacks)

        entries = LedgerEntry.objects.filter(key=f"firmware.approved:{pending.pk}")
        assert entries.count() == 1
        entry = entries.get()
        assert entry.wallet.user == uploader
        assert entry.amount == 10
        assert entry.reference == f"{type(firmware).__name__}:{firmware.pk}"

    @pytest.mark.parametrize(
        ("tier", "price", "event", "credits"),
        [
            ("paid", Decimal("5.00"), reward_events.APPROVED_PAID_EVENT, 15),
            ("featured", None, reward_events.APPROVED_FEATURED_EVENT, 25),
        ],
    )
    def test_paid_and_featured_uploads_use_their_own_rule(
        self,
        make_user,
        catalog,
        monkeypatch,
        django_capture_on_commit_callbacks,
        tier,
        price,
        event,
        credits,
    ):
        monkeypatch.setattr("apps.firmwares.services.is_seller", lambda user: True)
        uploader, reviewer = make_user("tier-earner"), make_user("approver2")
        reviewer.is_staff = reviewer.is_superuser = True
        reviewer.save()
        pending = pending_upload(catalog, uploader)

        approve(
            pending,
            reviewer,
            django_capture_on_commit_callbacks,
            access_tier=tier,
            approved_price=price,
        )

        entry = LedgerEntry.objects.get(key=f"firmware.approved:{pending.pk}")
        assert entry.event_key == event
        assert entry.amount == credits

    def test_reviewers_never_reward_their_own_uploads(
        self, make_user, catalog, django_capture_on_commit_callbacks
    ):
        staff = make_user("self-approver", superuser=True)
        pending = pending_upload(catalog, staff)

        approve(pending, staff, django_capture_on_commit_callbacks)

        assert not LedgerEntry.objects.filter(
            key=f"firmware.approved:{pending.pk}"
        ).exists()

    def test_an_approval_without_an_uploader_is_not_rewarded(
        self, make_user, catalog, django_capture_on_commit_callbacks
    ):
        uploader, reviewer = make_user("gone"), make_user("approver3", superuser=True)
        pending = pending_upload(catalog, uploader)
        type(pending).objects.filter(pk=pending.pk).update(uploader=None)
        pending.refresh_from_db()

        approve(pending, reviewer, django_capture_on_commit_callbacks)

        assert not LedgerEntry.objects.exists()

    def test_the_event_payload_is_stable(
        self, make_user, catalog, published, django_capture_on_commit_callbacks
    ):
        uploader, reviewer = (
            make_user("payload"),
            make_user("approver4", superuser=True),
        )
        pending = pending_upload(catalog, uploader)

        firmware = approve(pending, reviewer, django_capture_on_commit_callbacks)

        (payload,) = published
        assert payload["firmware"] == firmware
        assert payload["pending"].pk == pending.pk
        assert payload["uploader"] == uploader
        assert (payload["brand_id"], payload["model_id"], payload["variant_id"]) == (
            catalog.brand.pk,
            catalog.model.pk,
            catalog.variant.pk,
        )
        assert payload["firmware_type"] == "official"
        assert payload["access_tier"] == "free"

    def test_the_referral_event_names_the_uploader(
        self, make_user, catalog, contributions, django_capture_on_commit_callbacks
    ):
        uploader, reviewer = (
            make_user("referred"),
            make_user("approver7", superuser=True),
        )
        pending = pending_upload(catalog, uploader)

        approve(pending, reviewer, django_capture_on_commit_callbacks)

        assert contributions == [{"user_id": uploader.pk, "kind": "firmware"}]

    def test_a_failed_approval_emits_no_event_and_no_reward(
        self,
        make_user,
        catalog,
        published,
        contributions,
        django_capture_on_commit_callbacks,
    ):
        uploader, reviewer = make_user("failed"), make_user("approver5", superuser=True)
        pending = pending_upload(catalog, uploader)
        Path(pending.stored_file_path).unlink()

        with (
            pytest.raises(ValidationError),
            django_capture_on_commit_callbacks(execute=True),
        ):
            approve_pending_firmware(pending, reviewer, category="official")

        pending.refresh_from_db()
        assert pending.admin_decision == "pending"
        assert published == []
        assert contributions == []
        assert not LedgerEntry.objects.exists()

    def test_an_approved_file_fulfils_matching_requests_and_rewards_the_uploader(
        self, make_user, catalog, django_capture_on_commit_callbacks
    ):
        uploader, reviewer = (
            make_user("fulfiller"),
            make_user("approver6", superuser=True),
        )
        asker = make_user("asker")
        wanted = FirmwareRequest.objects.create(
            brand=catalog.brand,
            model=catalog.model,
            firmware_type="official",
            requested_by=asker,
        )
        other_type = FirmwareRequest.objects.create(
            brand=catalog.brand,
            model=catalog.model,
            firmware_type="engineering",
            requested_by=asker,
        )
        pending = pending_upload(catalog, uploader)

        firmware = approve(pending, reviewer, django_capture_on_commit_callbacks)

        wanted.refresh_from_db()
        other_type.refresh_from_db()
        assert wanted.status == "fulfilled"
        assert wanted.fulfilled_firmware_id == firmware.pk
        assert other_type.status == "open"
        fulfilled = LedgerEntry.objects.get(
            key=f"{reward_events.REQUEST_FULFILLED_EVENT}:{wanted.pk}"
        )
        assert fulfilled.wallet.user == uploader
