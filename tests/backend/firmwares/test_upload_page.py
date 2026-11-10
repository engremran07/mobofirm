"""The end-user upload page: required Brand -> Model -> Variant, tiers and review state."""

from __future__ import annotations

import re
import tempfile
import types
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.firmwares.models import (
    AccessTier,
    Brand,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from apps.firmwares.services import approve_pending_firmware


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
        "variant": catalog.variant.pk if catalog.variant else "",
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
class TestOptionalVariant:
    """The variant is required only when the chosen model has active variants."""

    def test_a_model_without_variants_accepts_an_upload_without_one(self, make_user):
        brand = Brand.objects.create(name="Plain", slug="plain")
        model = Model.objects.create(
            brand=brand, name="No Variants", slug="no-variants"
        )
        data = payload(types.SimpleNamespace(brand=brand, model=model, variant=None))
        data.pop("variant")

        response = client_for(make_user("plain-uploader")).post(
            reverse("firmwares:upload"), data
        )

        assert response.status_code == 302
        pending = PendingFirmware.objects.get()
        assert pending.uploaded_model == model
        assert pending.uploaded_variant is None

    def test_a_model_with_active_variants_needs_one(self, make_user, catalog):
        data = payload(catalog)
        data.pop("variant")

        response = client_for(make_user("lazy-uploader")).post(
            reverse("firmwares:upload"), data
        )

        assert response.status_code == 200
        assert "This model has variants" in response.content.decode()
        assert not PendingFirmware.objects.exists()

    def test_inactive_variants_do_not_make_the_variant_required(
        self, make_user, catalog
    ):
        Variant.objects.filter(pk=catalog.variant.pk).update(is_active=False)
        data = payload(catalog)
        data.pop("variant")

        response = client_for(make_user("retired-variant")).post(
            reverse("firmwares:upload"), data
        )

        assert response.status_code == 302
        assert PendingFirmware.objects.get().uploaded_variant is None

    def test_the_fragment_adapts_to_the_chosen_model(self, make_user, catalog):
        brand = Brand.objects.create(name="Plain", slug="plain")
        bare = Model.objects.create(brand=brand, name="Bare", slug="bare")
        client = client_for(make_user("fragment-user"))
        url = reverse("firmwares:upload_variant_field")

        waiting = client.get(url).content.decode()
        with_variants = client.get(url, {"model": catalog.model.pk}).content.decode()
        without = client.get(url, {"model": bare.pk}).content.decode()

        assert "Choose the brand and model first" in waiting
        assert 'data-depends-on="upload-model"' in with_variants
        assert "No specific variant" not in with_variants
        assert "No specific variant" in without
        assert 'name="variant" value=""' in without
        assert "data-depends-on" not in without

    def test_the_fragment_keeps_the_chosen_variant_of_the_model_only(
        self, make_user, catalog
    ):
        other = Model.objects.create(brand=catalog.brand, name="Other", slug="other")
        foreign = Variant.objects.create(model=other, name="Foreign", slug="foreign")
        client = client_for(make_user("keeper"))
        url = reverse("firmwares:upload_variant_field")

        kept = client.get(
            url, {"model": catalog.model.pk, "variant": catalog.variant.pk}
        ).content.decode()
        dropped = client.get(
            url, {"model": catalog.model.pk, "variant": foreign.pk}
        ).content.decode()

        assert f'value="{catalog.variant.pk}"' in kept
        assert 'value="Global"' in kept
        assert f'value="{foreign.pk}"' not in dropped

    @pytest.mark.usefixtures("catalog")
    def test_the_page_renders_the_state_of_a_preselected_model(self, make_user):
        brand = Brand.objects.create(name="Plain", slug="plain")
        bare = Model.objects.create(brand=brand, name="Bare", slug="bare")

        page = client_for(make_user("preselect")).get(
            reverse("firmwares:upload"), {"brand": brand.pk, "model": bare.pk}
        )

        assert "No specific variant" in page.content.decode()

    def test_a_variantless_upload_can_be_approved_and_published(
        self, make_user, django_user_model
    ):
        brand = Brand.objects.create(name="Plain", slug="plain")
        model = Model.objects.create(brand=brand, name="Solo", slug="solo")
        data = payload(types.SimpleNamespace(brand=brand, model=model, variant=None))
        client_for(make_user("solo-uploader")).post(reverse("firmwares:upload"), data)
        reviewer = django_user_model.objects.create_superuser(
            username="solo-reviewer", email="solo-reviewer@example.test"
        )

        approve_pending_firmware(
            PendingFirmware.objects.get(), reviewer, category="official"
        )

        firmware = OfficialFirmware.objects.get()
        assert (firmware.model, firmware.variant) == (model, None)

    def test_the_fragment_needs_a_signed_in_user(self):
        response = Client().get(reverse("firmwares:upload_variant_field"))

        assert response.status_code == 302


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
        assert body.count("data-depends-on=") == 1
        assert 'id="upload-variant-region"' in body
        assert reverse("firmwares:upload_variant_field") in body
        for kind in ("brand", "model"):
            assert f'"kinds": "{kind}"' in body

    def test_non_sellers_see_access_control_locked_to_free_with_a_seller_link(
        self, make_user
    ):
        response = client_for(make_user("page-user-2")).get(reverse("firmwares:upload"))

        body = response.content.decode()
        form = response.context["form"]
        assert "Access control" in body
        assert 'name="access_tier"' in body
        assert 'name="proposed_price"' not in body
        select = re.search(r'<select name="access_tier".*?</select>', body, re.DOTALL)
        assert select is not None
        options = re.findall(r"<option ([^>]*)>(\w+)</option>", select.group(0))
        assert [(label, "disabled" in attrs) for attrs, label in options] == [
            ("Free", False),
            ("Paid", True),
            ("Featured", True),
        ]
        assert "selected" in options[0][0]
        assert form.initial["access_tier"] == AccessTier.FREE
        assert (
            "Only approved sellers can offer paid or featured files — "
            f'<a href="{reverse("marketplace:become_seller")}">Become a seller</a>'
        ) in body

    def test_the_become_seller_link_follows_the_marketplace_flag(self, make_user):
        from apps.core.models import AppRegistry

        registry = AppRegistry.get_solo()
        registry.marketplace_enabled = False
        registry.save()

        body = (
            client_for(make_user("page-user-3"))
            .get(reverse("firmwares:upload"))
            .content.decode()
        )

        assert "Only approved sellers can offer paid or featured files" in body
        assert "Become a seller" not in body

    def test_sellers_get_the_live_tier_select_and_the_price_field(
        self, make_user, sellers
    ):
        seller = make_user("seller-page")
        sellers.add(seller.pk)

        body = client_for(seller).get(reverse("firmwares:upload")).content.decode()

        select = re.search(r'<select name="access_tier".*?</select>', body, re.DOTALL)
        assert select is not None and "disabled" not in select.group(0)
        assert 'name="proposed_price"' in body
        assert 'min="0.01"' in body and "data-price-field" in body
        assert "Become a seller" not in body

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

    @pytest.mark.parametrize("amount", ["0", "0.00", "-3"])
    def test_a_paid_proposal_must_be_above_zero(
        self, make_user, catalog, sellers, amount
    ):
        seller = make_user("seller-bad-price")
        sellers.add(seller.pk)

        response = client_for(seller).post(
            reverse("firmwares:upload"),
            payload(catalog, access_tier="paid", proposed_price=amount),
        )

        assert response.status_code == 200
        assert response.context["form"].errors["proposed_price"]
        assert not PendingFirmware.objects.exists()

    def test_sellers_may_submit_a_featured_or_free_file_without_a_price(
        self, make_user, catalog, sellers
    ):
        seller = make_user("seller-featured")
        sellers.add(seller.pk)

        response = client_for(seller).post(
            reverse("firmwares:upload"), payload(catalog, access_tier="featured")
        )

        assert response.status_code == 302
        pending = PendingFirmware.objects.get()
        assert (pending.access_tier, pending.proposed_price) == (
            AccessTier.FEATURED,
            None,
        )

    @pytest.mark.parametrize("forged", ["paid", "featured"])
    def test_a_forged_tier_from_a_non_seller_is_ignored(
        self, make_user, catalog, forged
    ):
        response = client_for(make_user(f"forger-{forged}")).post(
            reverse("firmwares:upload"),
            payload(catalog, access_tier=forged, proposed_price="5.00"),
        )

        assert response.status_code == 302
        pending = PendingFirmware.objects.get()
        assert (pending.access_tier, pending.proposed_price) == (AccessTier.FREE, None)

    def test_my_uploads_lists_only_the_users_own_submissions(self, make_user, catalog):
        owner, other = make_user("owner-uploads"), make_user("other-uploads")
        client_for(owner).post(reverse("firmwares:upload"), payload(catalog))

        mine = client_for(owner).get(reverse("firmwares:my_uploads"))
        theirs = client_for(other).get(reverse("firmwares:my_uploads"))

        assert "build.zip" in mine.content.decode()
        assert "build.zip" not in theirs.content.decode()
