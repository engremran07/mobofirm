"""The end-user upload page: required Brand -> Model -> Variant, tiers and review state."""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.firmwares.models import AccessTier, Brand, Model, PendingFirmware, Variant


@pytest.fixture(autouse=True)
def stage(settings):
    with (
        tempfile.TemporaryDirectory() as directory,
        patch(
            "apps.firmwares.ai_analysis.FirmwareAIAnalyzer._ai_based_analysis",
            return_value={"confidence": 0.0, "method": "ai"},
        ),
    ):
        settings.FIRMWARE_STORAGE_ROOT = Path(directory)
        yield


def client_for(user):
    client = Client()
    client.force_login(user)
    return client


def payload(catalog, **extra):
    values = {
        "brand": catalog.brand.pk,
        "model": catalog.model.pk,
        "variant": catalog.variant.pk,
        "source": "file",
        "file": SimpleUploadedFile(
            "build.zip", b"firmware-bytes", content_type="application/zip"
        ),
        "build_number": "BUILD.1",
        "package_format": "unknown",
    }
    values.update(extra)
    return values


@pytest.mark.django_db
class TestUploadPage:
    url = "/firmwares/upload/"

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        response = Client().get(reverse("firmwares:upload"))

        assert response.status_code == 302
        assert "/login" in response["Location"]

    def test_the_page_offers_the_cascade_and_request_links(self, make_user):
        response = client_for(make_user("page-user")).get(reverse("firmwares:upload"))

        body = response.content.decode()
        assert response.status_code == 200
        assert body.count("data-depends-on=") == 2
        for kind in ("brand", "model", "variant"):
            assert f'"kinds": "{kind}"' in body
        assert reverse("firmwares:request_variant") in body
        assert "access_tier" not in body

    def test_a_valid_upload_is_pending_and_linked_to_the_exact_variant(
        self, make_user, catalog
    ):
        user = make_user("uploader")

        response = client_for(user).post(reverse("firmwares:upload"), payload(catalog))

        assert response.status_code == 302
        pending = PendingFirmware.objects.get()
        assert pending.uploader == user
        assert (pending.uploaded_brand, pending.uploaded_model) == (
            catalog.brand,
            catalog.model,
        )
        assert pending.uploaded_variant == catalog.variant
        assert pending.admin_decision == "pending"
        assert (pending.access_tier, pending.proposed_price) == (AccessTier.FREE, None)

    @pytest.mark.parametrize("missing", ["brand", "model", "variant"])
    def test_every_level_of_the_cascade_is_required(self, make_user, catalog, missing):
        data = payload(catalog)
        data.pop(missing)

        response = client_for(make_user(f"no-{missing}")).post(
            reverse("firmwares:upload"), data
        )

        assert response.status_code == 200
        assert not PendingFirmware.objects.exists()

    def test_a_variant_of_another_model_is_refused(self, make_user, catalog):
        other = Model.objects.create(brand=catalog.brand, name="Other", slug="other")
        foreign = Variant.objects.create(model=other, name="Intl", slug="intl")

        response = client_for(make_user("mixed")).post(
            reverse("firmwares:upload"), payload(catalog, variant=foreign.pk)
        )

        assert "does not belong to the chosen model" in response.content.decode()
        assert not PendingFirmware.objects.exists()

    def test_a_model_of_another_brand_is_refused(self, make_user, catalog):
        other = Brand.objects.create(name="Other", slug="other")

        response = client_for(make_user("wrong-brand")).post(
            reverse("firmwares:upload"), payload(catalog, brand=other.pk)
        )

        assert "does not belong to the chosen brand" in response.content.decode()
        assert not PendingFirmware.objects.exists()

    def test_normal_users_cannot_make_an_upload_paid(self, make_user, catalog):
        response = client_for(make_user("sneaky")).post(
            reverse("firmwares:upload"),
            payload(catalog, access_tier="paid", proposed_price="9.00"),
        )

        assert response.status_code == 302
        pending = PendingFirmware.objects.get()
        assert (pending.access_tier, pending.proposed_price) == (AccessTier.FREE, None)

    def test_unverified_users_are_not_allowed_to_upload(self, make_user, catalog):
        response = client_for(make_user("unverified", verified=False)).post(
            reverse("firmwares:upload"), payload(catalog)
        )

        assert response.status_code == 302
        assert not PendingFirmware.objects.exists()

    def test_sellers_may_propose_a_paid_tier_but_never_set_the_price(
        self, make_user, catalog, sellers
    ):
        seller = make_user("seller")
        sellers.add(seller.pk)
        client = client_for(seller)
        page = client.get(reverse("firmwares:upload"))

        response = client.post(
            reverse("firmwares:upload"),
            payload(catalog, access_tier="paid", proposed_price="12.50"),
        )

        assert "access_tier" in page.content.decode()
        assert response.status_code == 302
        pending = PendingFirmware.objects.get()
        assert pending.access_tier == AccessTier.PAID
        assert pending.proposed_price == Decimal("12.50")
        assert pending.approved_price is None

    def test_a_paid_proposal_needs_a_price(self, make_user, catalog, sellers):
        seller = make_user("seller-no-price")
        sellers.add(seller.pk)

        response = client_for(seller).post(
            reverse("firmwares:upload"), payload(catalog, access_tier="paid")
        )

        assert "proposed price" in response.content.decode().lower()
        assert not PendingFirmware.objects.exists()

    def test_my_uploads_lists_only_the_users_own_submissions(self, make_user, catalog):
        owner, other = make_user("owner-uploads"), make_user("other-uploads")
        client_for(owner).post(reverse("firmwares:upload"), payload(catalog))

        mine = client_for(owner).get(reverse("firmwares:my_uploads"))
        theirs = client_for(other).get(reverse("firmwares:my_uploads"))

        assert "build.zip" in mine.content.decode()
        assert "build.zip" not in theirs.content.decode()
