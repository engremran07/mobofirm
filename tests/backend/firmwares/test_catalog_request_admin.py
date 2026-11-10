"""Admin review queues for catalogue requests: population, KPI and decisions."""

from __future__ import annotations

import pytest
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse

from apps.core import admin_dashboard
from apps.core.signals import notification_send_requested
from apps.firmwares import catalog_requests as service
from apps.firmwares.constants import RESERVED_BRAND_SLUGS
from apps.firmwares.models import (
    Brand,
    BrandCreationRequest,
    Chipset,
    ChipsetCreationRequest,
    Model,
    ModelCreationRequest,
    Variant,
    VariantCreationRequest,
)
from apps.firmwares.tracking_models import FirmwareRequest
from apps.firmwares.variant_rules import VariantIdentity

DIALOG = {"_form_submitted": "on"}


def staff_client(user):
    """A test client signed in as ``user``."""
    client = Client()
    client.force_login(user)
    return client


def action_url(model: str, name: str, pk: int) -> str:
    """URL of a row/detail action of a request queue."""
    return reverse(f"admin:firmwares_{model}_{name}", args=[pk])


def changelist(model: str) -> str:
    """URL of a request queue changelist."""
    return reverse(f"admin:firmwares_{model}_changelist")


@pytest.fixture(name="reviewer")
def reviewer_fixture(make_user):
    """A superuser able to decide every queue."""
    return make_user("queue-reviewer", superuser=True)


@pytest.fixture(name="requester")
def requester_fixture(make_user):
    """The end user who files the requests."""
    return make_user("queue-requester")


class Notices(list):
    """The notifications sent so far, plus a way to run the on-commit hooks."""

    def __init__(self, capture) -> None:
        super().__init__()
        self._capture = capture

    def commit(self):
        """Context manager that runs the ``on_commit`` callbacks when it exits."""
        return self._capture(execute=True)


@pytest.fixture(name="sent")
def sent_fixture(django_capture_on_commit_callbacks):
    """Collect the moderation notifications sent while a test runs."""
    received = Notices(django_capture_on_commit_callbacks)

    def collect(sender, **kwargs):
        received.append({"sender": sender, **kwargs})

    notification_send_requested.connect(collect, weak=False)
    yield received
    notification_send_requested.disconnect(collect)


@pytest.mark.django_db
class TestQueuePopulation:
    """The queues list what users file, waiting requests first."""

    def test_a_submitted_model_request_is_listed_and_counted(
        self, make_user, catalog, reviewer
    ):
        """Regression: a filed model request appears in the queue and the count."""
        cache.clear()
        service.submit_model_request(
            make_user("model-asker"), brand=catalog.brand, name="Fresh Phone"
        )

        client = staff_client(reviewer)
        listing = client.get(changelist("modelcreationrequest"))
        pending = client.get(
            changelist("modelcreationrequest"), {"status__exact": "pending"}
        )
        metrics = admin_dashboard.compute_metrics()

        assert ModelCreationRequest.objects.count() == 1
        assert "Fresh Phone" in listing.content.decode()
        assert "Fresh Phone" in pending.content.decode()
        assert metrics["pending_model_requests"] == 1

    def test_waiting_requests_are_listed_before_decided_ones(
        self, make_user, catalog, reviewer
    ):
        """Decided requests do not push the work that is waiting off the page."""
        asker = make_user("order-asker")
        old = service.submit_model_request(asker, brand=catalog.brand, name="Old One")
        service.reject_catalog_request(old, reviewer, reason="Nope")
        service.submit_model_request(asker, brand=catalog.brand, name="Waiting One")

        response = staff_client(reviewer).get(changelist("modelcreationrequest"))

        text = response.content.decode()
        assert text.index("Waiting One") < text.index("Old One")

    def test_a_pending_request_can_be_corrected_but_a_decided_one_is_frozen(
        self, make_user, catalog, reviewer
    ):
        """The old review-only behaviour is gone: pending data is editable."""
        pending = service.submit_model_request(
            make_user("edit-asker"), brand=catalog.brand, name="Typo Phone"
        )
        client = staff_client(reviewer)
        url = reverse("admin:firmwares_modelcreationrequest_change", args=[pending.pk])

        open_form = client.get(url).content.decode()
        service.approve_model_request(pending, reviewer)
        frozen_form = client.get(url).content.decode()

        assert 'name="name"' in open_form
        assert 'name="model_code"' in open_form
        assert 'name="name"' not in frozen_form


@pytest.mark.django_db
class TestCatalogKpi:
    """The dashboard card agrees with the list behind it."""

    def kpi(self, user):
        """The catalogue-requests KPI as ``user`` sees it."""
        metrics = admin_dashboard.get_metrics()
        return {k.key: k for k in admin_dashboard.build_kpis(metrics, user)}[
            "catalog_requests"
        ]

    def test_the_kpi_opens_a_queue_that_holds_pending_requests(
        self, make_user, reviewer
    ):
        """With only a brand request waiting, the card opens the brand queue."""
        cache.clear()
        service.submit_brand_request(make_user("brand-asker"), name="Zenith")

        kpi = self.kpi(reviewer)
        listing = staff_client(reviewer).get(kpi.url)

        assert kpi.value == 1
        assert "brandcreationrequest" in kpi.url
        assert "Zenith" in listing.content.decode()

    def test_the_count_refreshes_as_soon_as_a_request_is_filed(
        self, make_user, catalog, reviewer
    ):
        """Filing a request invalidates the cached dashboard numbers."""
        cache.clear()
        assert self.kpi(reviewer).value == 0

        service.submit_model_request(
            make_user("quick-asker"), brand=catalog.brand, name="Quick Phone"
        )

        assert self.kpi(reviewer).value == 1

    def test_the_count_follows_the_queues_the_staff_member_may_view(
        self, make_user, catalog
    ):
        """A reviewer of one queue sees that queue's count and link only."""
        cache.clear()
        asker = make_user("mixed-asker")
        service.submit_brand_request(asker, name="Orion")
        service.submit_model_request(asker, brand=catalog.brand, name="Orion One")
        staff = make_user("model-queue-only", staff=True)
        staff.user_permissions.add(
            Permission.objects.get(codename="view_modelcreationrequest")
        )

        kpi = self.kpi(staff)

        assert kpi.value == 1
        assert "modelcreationrequest" in kpi.url
        assert kpi.permission == "firmwares.view_modelcreationrequest"


@pytest.mark.django_db
class TestApproveDialogs:
    """Approve creates or links the real record, once, with audit and a notice."""

    def test_brand_approval_applies_the_reviewers_corrections(
        self, requester, reviewer, sent
    ):
        """Name and slug typed in the dialog win over the requested data."""
        pending = service.submit_brand_request(requester, name="Xiomi")

        with sent.commit():
            response = staff_client(reviewer).post(
                action_url("brandcreationrequest", "approve_request", pending.pk),
                {**DIALOG, "name": "Xiaomi", "slug": "mi-brand", "reason": "Fixed"},
            )

        brand = Brand.objects.get(slug="mi-brand")
        pending.refresh_from_db()
        assert "HX-Redirect" in response.headers
        assert brand.name == "Xiaomi"
        assert (pending.status, pending.reviewed_by) == ("approved", reviewer)
        assert pending.reviewed_at is not None
        assert [item["user_ids"] for item in sent] == [[requester.pk]]
        assert "approved" in sent[0]["message"]
        assert "Fixed" in sent[0]["message"]

    def test_approval_is_audited_for_the_request_and_the_new_record(
        self, requester, reviewer
    ):
        """The admin history records the creation and the decision."""
        pending = service.submit_brand_request(requester, name="Audited")

        staff_client(reviewer).post(
            action_url("brandcreationrequest", "approve_request", pending.pk), DIALOG
        )

        flags = {
            (entry.content_type.model, entry.action_flag)
            for entry in LogEntry.objects.filter(user=reviewer)
        }
        assert ("brand", ADDITION) in flags
        assert ("brandcreationrequest", CHANGE) in flags

    def test_approving_twice_creates_one_record(self, requester, reviewer):
        """A repeated approval is refused and creates nothing more."""
        pending = service.submit_brand_request(requester, name="Once")
        url = action_url("brandcreationrequest", "approve_request", pending.pk)
        client = staff_client(reviewer)

        client.post(url, DIALOG)
        second = client.post(url, DIALOG)

        assert Brand.objects.filter(name="Once").count() == 1
        assert "HX-Redirect" in second.headers

    def test_model_approval_keeps_brand_and_chipset(self, make_user, catalog, reviewer):
        """The model is created under the requested brand with its chipset."""
        chipset = Chipset.objects.create(
            vendor="MediaTek", name="Dimensity 9200", slug="mediatek-dimensity-9200"
        )
        pending = service.submit_model_request(
            make_user("model-dialog"),
            brand=catalog.brand,
            name="Phone Nine",
            model_code="AC-9",
            chipset=chipset,
        )

        staff_client(reviewer).post(
            action_url("modelcreationrequest", "approve_request", pending.pk),
            {**DIALOG, "model_code": "AC-9X"},
        )

        model = Model.objects.get(brand=catalog.brand, name="Phone Nine")
        assert (model.chipset_ref, model.model_code) == (chipset, "AC-9X")

    def test_variant_approval_normalises_the_code_and_reads_memory(
        self, make_user, catalog, reviewer
    ):
        """The variant gets an upper-case code and parsed RAM/storage options."""
        pending = service.submit_variant_request(
            make_user("variant-dialog"),
            model=catalog.model,
            identity=VariantIdentity(
                model_code="ai2401 a", memory="8 GB / 256 GB", carrier="Orange"
            ),
        )

        staff_client(reviewer).post(
            action_url("variantcreationrequest", "approve_request", pending.pk),
            DIALOG,
        )

        variant = Variant.objects.get(model=catalog.model, model_code="AI2401_A")
        assert variant.carrier == "Orange"
        assert (variant.ram_options, variant.storage_options) == ([8], [256])

    def test_chipset_approval_creates_the_chipset(self, requester, reviewer):
        """The chipset is created from the requested vendor and name."""
        pending = service.submit_chipset_request(
            requester, vendor="MediaTek", name="Dimensity 9200 (MT6985)"
        )

        staff_client(reviewer).post(
            action_url("chipsetcreationrequest", "approve_request", pending.pk),
            DIALOG,
        )

        assert Chipset.objects.filter(vendor="MediaTek").count() == 1
        pending.refresh_from_db()
        assert pending.status == "approved"

    def test_an_existing_record_is_linked_instead_of_duplicated(
        self, make_user, catalog, reviewer
    ):
        """A request for something that appeared meanwhile links to it."""
        pending = service.submit_model_request(
            make_user("late"), brand=catalog.brand, name="Late Phone"
        )
        existing = Model.objects.create(
            brand=catalog.brand, name="Late Phone", slug="late-phone"
        )

        model, created = service.approve_model_request(pending, reviewer)

        assert (model, created) == (existing, False)
        assert Model.objects.filter(brand=catalog.brand).count() == 2

    def test_a_failed_approval_changes_nothing(self, reviewer, requester):
        """Reserved names abort the whole transaction: no record, still pending."""
        reserved_name = RESERVED_BRAND_SLUGS[0]
        reserved = BrandCreationRequest.objects.create(
            name=reserved_name, requested_by=requester
        )

        staff_client(reviewer).post(
            action_url("brandcreationrequest", "approve_request", reserved.pk), DIALOG
        )

        reserved.refresh_from_db()
        assert reserved.status == "pending"
        assert not Brand.objects.filter(slug=reserved_name).exists()
        assert not LogEntry.objects.filter(user=reviewer).exists()

    def test_the_dialog_is_prefilled_from_the_request(self, requester, reviewer):
        """GET shows the requested data so the reviewer can correct it."""
        pending = service.submit_brand_request(requester, name="Prefilled")

        page = staff_client(reviewer).get(
            action_url("brandcreationrequest", "approve_request", pending.pk)
        )

        body = page.content.decode()
        assert 'value="Prefilled"' in body
        assert 'placeholder="Xiaomi"' in body

    def test_firmware_request_approval_creates_the_variant_and_notifies(
        self, make_user, catalog, reviewer, sent
    ):
        """Approving accepts the request and creates the missing variant."""
        asker = make_user("fw-asker")
        request, _created = service.submit_firmware_request(
            asker, brand=catalog.brand, model=catalog.model, variant_name="AI2401_D"
        )

        with sent.commit():
            staff_client(reviewer).post(
                action_url("firmwarerequest", "approve_request", request.pk),
                {**DIALOG, "variant_name": "AI2401_D"},
            )

        request.refresh_from_db()
        assert request.status == "in_progress"
        assert Variant.objects.filter(model=catalog.model, name="AI2401_D").exists()
        assert [item["user_ids"] for item in sent] == [[asker.pk]]


@pytest.mark.django_db
class TestRejectDialogs:
    """Reject needs a reason, stores it and tells the requester."""

    def test_the_reason_is_required_then_stored_and_sent(
        self, make_user, catalog, reviewer, sent
    ):
        """An empty reason keeps the dialog open; a real one closes the request."""
        pending = service.submit_variant_request(
            make_user("rejected-asker"),
            model=catalog.model,
            identity=VariantIdentity(region="India"),
        )
        url = action_url("variantcreationrequest", "reject_request", pending.pk)
        client = staff_client(reviewer)

        missing = client.post(url, DIALOG)
        with sent.commit():
            done = client.post(url, {**DIALOG, "reason": "Not a real variant"})

        pending.refresh_from_db()
        assert "HX-Redirect" not in missing.headers
        assert "HX-Redirect" in done.headers
        assert (pending.status, pending.notes) == ("rejected", "Not a real variant")
        assert pending.reviewed_by == reviewer
        assert "Not a real variant" in sent[0]["message"]
        assert not Variant.objects.filter(model=catalog.model, region="India").exists()

    def test_every_queue_has_a_reject_action(self, make_user, catalog, reviewer):
        """Brand, chipset and firmware requests all reject through the dialog."""
        asker = make_user("every-asker")
        firmware, _created = service.submit_firmware_request(
            asker, brand=catalog.brand, model=catalog.model
        )
        client = staff_client(reviewer)

        for model, pending in (
            ("brandcreationrequest", service.submit_brand_request(asker, name="Rej")),
            (
                "chipsetcreationrequest",
                service.submit_chipset_request(asker, vendor="Acme", name="C1"),
            ),
            ("firmwarerequest", firmware),
        ):
            client.post(
                action_url(model, "reject_request", pending.pk),
                {**DIALOG, "reason": "No"},
            )
            pending.refresh_from_db()
            assert pending.status == "rejected"


@pytest.mark.django_db
class TestBulkActions:
    """Bulk approve/reject confirm on an admin page, then apply to every row."""

    def post_action(self, client, model, action, ids, **extra):
        """POST a changelist bulk action like the admin form does."""
        return client.post(
            changelist(model),
            {
                "action": action,
                "_selected_action": [str(pk) for pk in ids],
                "select_across": "0",
                "index": "0",
                **extra,
            },
        )

    def test_bulk_approve_asks_for_confirmation_before_changing_anything(
        self, make_user, reviewer
    ):
        """The first POST renders the confirm page and writes nothing."""
        asker = make_user("bulk-asker")
        rows = [service.submit_brand_request(asker, name=f"Bulk {n}") for n in "AB"]

        page = self.post_action(
            staff_client(reviewer),
            "brandcreationrequest",
            "approve_selected",
            [row.pk for row in rows],
        )

        body = page.content.decode()
        assert page.status_code == 200
        assert "Bulk A" in body
        assert 'name="confirm"' in body
        assert not Brand.objects.filter(name__startswith="Bulk").exists()

    def test_bulk_approve_creates_every_record_and_notifies_each_requester(
        self, make_user, reviewer, sent
    ):
        """Confirming approves all selected pending requests."""
        rows = [
            service.submit_brand_request(make_user(f"bulk-{n}"), name=f"Brand {n}")
            for n in "XY"
        ]

        with sent.commit():
            response = self.post_action(
                staff_client(reviewer),
                "brandcreationrequest",
                "approve_selected",
                [row.pk for row in rows],
                confirm="1",
                reason="Welcome",
            )

        statuses = set(
            BrandCreationRequest.objects.filter(
                pk__in=[row.pk for row in rows]
            ).values_list("status", flat=True)
        )
        assert response.status_code == 302
        assert Brand.objects.filter(name__in=["Brand X", "Brand Y"]).count() == 2
        assert statuses == {"approved"}
        assert len(sent) == 2

    def test_bulk_reject_requires_a_reason(self, make_user, reviewer):
        """Without a reason the confirm page returns with an error."""
        row = service.submit_chipset_request(
            make_user("bulk-chip"), vendor="Acme", name="Z1"
        )
        client = staff_client(reviewer)

        missing = self.post_action(
            client,
            "chipsetcreationrequest",
            "reject_selected",
            [row.pk],
            confirm="1",
            reason="",
        )
        done = self.post_action(
            client,
            "chipsetcreationrequest",
            "reject_selected",
            [row.pk],
            confirm="1",
            reason="Duplicate of another chipset",
        )

        row.refresh_from_db()
        assert missing.status_code == 200
        assert done.status_code == 302
        assert (row.status, row.notes) == ("rejected", "Duplicate of another chipset")

    def test_decided_requests_are_skipped(self, make_user, catalog, reviewer):
        """Selecting an already decided request does not decide it again."""
        row = service.submit_model_request(
            make_user("bulk-done"), brand=catalog.brand, name="Done Phone"
        )
        service.reject_catalog_request(row, reviewer, reason="No")

        response = self.post_action(
            staff_client(reviewer),
            "modelcreationrequest",
            "approve_selected",
            [row.pk],
            confirm="1",
        )

        row.refresh_from_db()
        assert response.status_code == 302
        assert row.status == "rejected"
        assert not Model.objects.filter(name="Done Phone").exists()

    def test_firmware_requests_support_bulk_decisions(
        self, make_user, catalog, reviewer
    ):
        """The firmware queue has the same bulk actions (open requests)."""
        row, _created = service.submit_firmware_request(
            make_user("bulk-fw"), brand=catalog.brand, model=catalog.model
        )

        self.post_action(
            staff_client(reviewer),
            "firmwarerequest",
            "approve_selected",
            [row.pk],
            confirm="1",
        )

        row.refresh_from_db()
        assert row.status == "in_progress"


@pytest.mark.django_db
class TestPermissions:
    """Only staff with the change permission decide a queue."""

    def test_view_only_staff_cannot_decide(self, make_user, requester):
        """A viewer gets 403 on the dialog and the bulk action decides nothing."""
        pending = service.submit_brand_request(requester, name="Guarded Brand")
        viewer = make_user("viewer", staff=True)
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_brandcreationrequest")
        )
        client = staff_client(viewer)

        row = client.post(
            action_url("brandcreationrequest", "approve_request", pending.pk), DIALOG
        )
        client.post(
            changelist("brandcreationrequest"),
            {
                "action": "approve_selected",
                "_selected_action": [str(pending.pk)],
                "index": "0",
                "confirm": "1",
            },
        )

        pending.refresh_from_db()
        assert row.status_code == 403
        assert pending.status == "pending"

    def test_the_service_refuses_users_without_the_permission(
        self, make_user, requester
    ):
        """The services enforce the permission for every entry point."""
        pending = service.submit_brand_request(requester, name="Service Guard")

        with pytest.raises(PermissionDenied, match="Review permission"):
            service.approve_brand_request(pending, make_user("not-staff"))

    def test_the_queues_have_no_add_form(self, reviewer):
        """The queues stay review queues: requests are filed by end users."""
        client = staff_client(reviewer)

        for model in (
            BrandCreationRequest,
            ModelCreationRequest,
            VariantCreationRequest,
            ChipsetCreationRequest,
            FirmwareRequest,
        ):
            name = model._meta.model_name
            response = client.get(reverse(f"admin:firmwares_{name}_add"))
            assert response.status_code == 403
