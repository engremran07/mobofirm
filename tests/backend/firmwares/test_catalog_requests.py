"""End-user catalogue requests: required parents, dedupe, approval and admin create flows."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from apps.firmwares import catalog_requests as service
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


def client_for(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture(name="chipset")
def chipset_fixture():
    return Chipset.objects.create(
        vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
    )


@pytest.fixture(name="reviewer")
def reviewer_fixture(make_user):
    return make_user("catalog-reviewer", superuser=True)


# --------------------------------------------------------------------------- pages


@pytest.mark.django_db
class TestRequestPages:
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = Client().get(reverse("firmwares:request_model"))

        assert response.status_code == 302
        assert "/login" in response["Location"]

    def test_unverified_users_cannot_submit(self, make_user, catalog):
        user = make_user("unverified", verified=False)

        response = client_for(user).post(
            reverse("firmwares:request_model"),
            {"brand": catalog.brand.pk, "name": "Ghost One"},
        )

        assert response.status_code == 302
        assert not ModelCreationRequest.objects.exists()

    def test_a_model_request_requires_the_brand(self, make_user):
        user = make_user("no-brand")

        response = client_for(user).post(
            reverse("firmwares:request_model"), {"name": "Orphan Model"}
        )

        assert response.status_code == 200
        assert "Choose the brand from the list." in response.content.decode()
        assert not ModelCreationRequest.objects.exists()

    def test_a_model_request_keeps_the_brand_and_the_optional_chipset(
        self, make_user, catalog, chipset
    ):
        user = make_user("model-requester")

        response = client_for(user).post(
            reverse("firmwares:request_model"),
            {
                "brand": catalog.brand.pk,
                "chipset": chipset.pk,
                "name": "Phone Two",
                "model_code": "AC-2",
            },
        )

        assert response.status_code == 302
        pending = ModelCreationRequest.objects.get()
        assert (pending.brand, pending.chipset, pending.requested_by) == (
            catalog.brand,
            chipset,
            user,
        )

    def test_the_chipset_is_optional(self, make_user, catalog):
        response = client_for(make_user("no-chipset")).post(
            reverse("firmwares:request_model"),
            {"brand": catalog.brand.pk, "name": "Plain Phone"},
        )

        assert response.status_code == 302
        assert ModelCreationRequest.objects.get().chipset is None

    def test_a_variant_request_requires_brand_and_model(self, make_user, catalog):
        client = client_for(make_user("variant-requester"))
        url = reverse("firmwares:request_variant")

        no_model = client.post(url, {"brand": catalog.brand.pk, "name": "India"})
        no_brand = client.post(url, {"model": catalog.model.pk, "name": "India"})

        assert "Choose the model from the list." in no_model.content.decode()
        assert "Choose the brand from the list." in no_brand.content.decode()
        assert not VariantCreationRequest.objects.exists()

    def test_a_variant_request_rejects_a_model_of_another_brand(
        self, make_user, catalog
    ):
        other = Brand.objects.create(name="Other", slug="other")

        response = client_for(make_user("mismatch")).post(
            reverse("firmwares:request_variant"),
            {"brand": other.pk, "model": catalog.model.pk, "name": "India"},
        )

        assert "does not belong to the chosen brand" in response.content.decode()
        assert not VariantCreationRequest.objects.exists()

    def test_a_valid_variant_request_is_stored_against_the_model(
        self, make_user, catalog
    ):
        response = client_for(make_user("variant-ok")).post(
            reverse("firmwares:request_variant"),
            {
                "brand": catalog.brand.pk,
                "model": catalog.model.pk,
                "name": "India",
                "region": "IN",
            },
        )

        assert response.status_code == 302
        assert VariantCreationRequest.objects.get().model == catalog.model

    def test_existing_entries_are_refused_with_a_hint(self, make_user, catalog):
        response = client_for(make_user("dupe")).post(
            reverse("firmwares:request_model"),
            {"brand": catalog.brand.pk, "name": "Phone One"},
        )

        assert response.status_code == 200
        assert "already exists in the catalogue" in response.content.decode()
        assert not ModelCreationRequest.objects.exists()

    def test_the_live_check_reports_existing_models_of_the_brand(
        self, make_user, catalog
    ):
        response = client_for(make_user("check")).get(
            reverse("firmwares:request_check"),
            {"kind": "model", "name": "Phone One", "brand": catalog.brand.pk},
        )

        assert response.status_code == 200
        assert "already exists" in response.content.decode()

    def test_my_requests_lists_status_and_reviewer_reason(
        self, make_user, catalog, reviewer
    ):
        user = make_user("lister")
        pending = service.submit_model_request(
            user, brand=catalog.brand, name="Listed Phone"
        )
        service.reject_catalog_request(pending, reviewer, reason="Not a real device")

        page = client_for(user).get(reverse("firmwares:my_requests"))

        body = page.content.decode()
        assert "Listed Phone" in body
        assert "Not a real device" in body

    def test_signed_out_empty_states_offer_a_request_link(self):
        response = Client().get(reverse("firmwares:brand_list"), {"q": "zzzz"})

        assert 'data-request-cta="brand"' in response.content.decode()


# --------------------------------------------------------------------------- services


@pytest.mark.django_db
class TestApproval:
    def test_approving_a_model_request_uses_the_requested_brand_and_chipset(
        self, make_user, catalog, chipset, reviewer
    ):
        pending = service.submit_model_request(
            make_user("approved-model"),
            brand=catalog.brand,
            name="Phone Three",
            chipset=chipset,
        )

        model, created = service.approve_model_request(pending, reviewer)

        assert created
        assert (model.brand, model.chipset_ref) == (catalog.brand, chipset)
        assert model.chipset == chipset.display_name
        pending.refresh_from_db()
        assert pending.status == "approved"
        assert pending.reviewed_by == reviewer

    def test_approving_a_variant_request_creates_it_under_the_requested_model(
        self, make_user, catalog, reviewer
    ):
        pending = service.submit_variant_request(
            make_user("approved-variant"), model=catalog.model, name="EU", region="EU"
        )

        variant, created = service.approve_variant_request(pending, reviewer)

        assert created
        assert variant.model == catalog.model
        assert variant.region == "EU"

    def test_a_decision_can_only_be_made_once(self, make_user, catalog, reviewer):
        pending = service.submit_model_request(
            make_user("twice"), brand=catalog.brand, name="Once Only"
        )
        service.approve_model_request(pending, reviewer)

        with pytest.raises(ValidationError):
            service.approve_model_request(pending, reviewer)

        assert Model.objects.filter(brand=catalog.brand, name="Once Only").count() == 1

    def test_only_reviewers_may_decide(self, make_user, catalog):
        pending = service.submit_model_request(
            make_user("owner"), brand=catalog.brand, name="Guarded"
        )

        with pytest.raises(PermissionDenied):
            service.approve_model_request(pending, make_user("plain-user"))

        pending.refresh_from_db()
        assert pending.status == "pending"

    def test_rejection_needs_a_reason(self, make_user, catalog, reviewer):
        pending = service.submit_model_request(
            make_user("rejected"), brand=catalog.brand, name="Nope"
        )

        with pytest.raises(ValidationError):
            service.reject_catalog_request(pending, reviewer, reason=" ")

        service.reject_catalog_request(pending, reviewer, reason="Duplicate")
        pending.refresh_from_db()
        assert (pending.status, pending.notes) == ("rejected", "Duplicate")

    def test_a_chipset_request_creates_the_chipset(self, make_user, reviewer):
        pending = service.submit_chipset_request(
            make_user("chipset-user"),
            vendor="Unisoc",
            name="T820",
            aliases="UNISOC T820, T-820",
        )

        created, _ = service.approve_chipset_request(pending, reviewer)

        assert Chipset.objects.get(pk=created.pk).aliases == [
            "UNISOC T820",
            "T-820",
        ]

    def test_requests_are_rate_limited(self, make_user, catalog, settings):
        settings.FIRMWARE_REQUESTS_PER_HOUR = 1
        user = make_user("spammer")
        service.submit_model_request(user, brand=catalog.brand, name="First Phone")

        with pytest.raises(service.RequestRateLimited):
            service.submit_model_request(user, brand=catalog.brand, name="Second Phone")

    def test_a_pending_duplicate_is_refused(self, make_user, catalog):
        service.submit_model_request(
            make_user("first"), brand=catalog.brand, name="Same Phone"
        )

        with pytest.raises(service.CatalogRequestError):
            service.submit_model_request(
                make_user("second"), brand=catalog.brand, name="same phone"
            )


# --------------------------------------------------------------------------- REST


@pytest.mark.django_db
class TestRequestApi:
    def test_the_variant_request_requires_a_brand_matching_the_model(
        self, make_user, catalog
    ):
        api = APIClient()
        api.force_authenticate(make_user("api-variant"))
        other = Brand.objects.create(name="Other", slug="other")
        url = reverse("firmwares:variant-requests-list")

        missing = api.post(
            url, {"model": catalog.model.pk, "name": "India"}, format="json"
        )
        mismatch = api.post(
            url,
            {"brand": other.pk, "model": catalog.model.pk, "name": "India"},
            format="json",
        )
        valid = api.post(
            url,
            {"brand": catalog.brand.pk, "model": catalog.model.pk, "name": "India"},
            format="json",
        )

        assert missing.status_code == 400
        assert mismatch.status_code == 400
        assert valid.status_code == 201, valid.content

    def test_the_model_request_accepts_an_optional_chipset(
        self, make_user, catalog, chipset
    ):
        api = APIClient()
        api.force_authenticate(make_user("api-model"))

        response = api.post(
            reverse("firmwares:model-requests-list"),
            {"brand": catalog.brand.pk, "chipset": chipset.pk, "name": "Api Phone"},
            format="json",
        )

        assert response.status_code == 201, response.content
        assert ModelCreationRequest.objects.get().chipset == chipset

    def test_staff_approve_through_the_same_service(self, make_user, catalog, reviewer):
        pending = service.submit_model_request(
            make_user("api-owner"), brand=catalog.brand, name="Api Approved"
        )
        api = APIClient()
        api.force_authenticate(reviewer)

        response = api.post(
            reverse("firmwares:model-requests-approve", args=[pending.pk]),
            {"reason": "ok"},
            format="json",
        )

        assert response.status_code == 200
        assert Model.objects.filter(brand=catalog.brand, name="Api Approved").exists()


# --------------------------------------------------------------------------- admin


@pytest.mark.django_db
class TestAdminCreateFlows:
    def test_request_queues_have_no_add_permission(self, reviewer):
        request = RequestFactory().get("/")
        request.user = reviewer

        for model in (
            BrandCreationRequest,
            ModelCreationRequest,
            VariantCreationRequest,
            ChipsetCreationRequest,
        ):
            assert not admin.site.get_model_admin(model).has_add_permission(request)

    def test_the_variant_form_needs_a_brand_and_a_model_of_that_brand(
        self, reviewer, catalog
    ):
        other = Brand.objects.create(name="Other", slug="other")
        client = client_for(reviewer)
        url = reverse("admin:firmwares_variant_add")
        data = {"name": "Admin Variant", "slug": "admin-variant", "is_active": "on"}

        no_brand = client.post(url, {**data, "model": catalog.model.pk})
        wrong = client.post(url, {**data, "brand": other.pk, "model": catalog.model.pk})
        valid = client.post(
            url, {**data, "brand": catalog.brand.pk, "model": catalog.model.pk}
        )

        assert no_brand.status_code == 200
        assert wrong.status_code == 200
        assert valid.status_code == 302, valid.content.decode()[:2000]
        assert Variant.objects.filter(slug="admin-variant").count() == 1

    def test_the_variant_form_offers_only_the_chosen_brands_models(
        self, reviewer, catalog
    ):
        other = Brand.objects.create(name="Other", slug="other")
        Model.objects.create(brand=other, name="Foreign Phone", slug="foreign")
        client = client_for(reviewer)

        options = client.get(
            reverse("admin:firmwares_variant_model_options"),
            {"brand": catalog.brand.pk},
        ).content.decode()
        nothing = client.get(
            reverse("admin:firmwares_variant_model_options"), {"brand": ""}
        ).content.decode()
        form = client.get(reverse("admin:firmwares_variant_add")).content.decode()

        assert "Phone One" in options
        assert "Foreign Phone" not in options
        assert "Phone One" not in nothing
        assert "model-options" in form

    def test_the_model_add_form_requires_a_brand_and_keeps_the_chipset_optional(
        self, reviewer, chipset
    ):
        client = client_for(reviewer)
        url = reverse("admin:firmwares_model_add")
        data = {
            "name": "Admin Phone",
            "slug": "admin-phone",
            "status": "available",
            "variants-TOTAL_FORMS": 0,
            "variants-INITIAL_FORMS": 0,
            "variants-MIN_NUM_FORMS": 0,
            "variants-MAX_NUM_FORMS": 1000,
        }

        without_brand = client.post(url, data)
        with_both = client.post(
            url,
            {
                **data,
                "brand": Brand.objects.create(name="B", slug="b").pk,
                "chipset_ref": chipset.pk,
            },
        )

        assert without_brand.status_code == 200
        assert with_both.status_code == 302, (
            with_both.context["adminform"].form.errors
            or with_both.context["inline_admin_formsets"][0].formset.errors
        )
        assert Model.objects.get(slug="admin-phone").chipset_ref == chipset
