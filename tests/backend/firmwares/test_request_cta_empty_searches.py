"""Every empty search or list offers the matching "Request a ..." button."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.firmwares.models import Variant

NOTHING = "zzqxnonexistent"
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog")]


def cta_kinds(response) -> set[str]:
    """The request kinds whose button appears in ``response``."""
    body = response.content.decode()
    return {
        kind
        for kind in ("brand", "model", "variant", "chipset", "firmware")
        if f'data-request-cta="{kind}"' in body
    }


def model_url(catalog) -> str:
    """The public page of the catalogue fixture's model."""
    return reverse(
        "firmwares:model_detail",
        kwargs={"brand_slug": catalog.brand.slug, "model_slug": catalog.model.slug},
    )


class TestEmptySearchesOfferRequests:
    """Searching for something that does not exist leads to the request form."""

    def test_the_brand_list_offers_a_brand_request(self):
        """No brand matches: the brand request button is shown."""
        response = Client().get(reverse("firmwares:brand_list"), {"q": NOTHING})

        assert "brand" in cta_kinds(response)

    def test_the_chipset_list_offers_a_chipset_request(self):
        """No chipset matches: the chipset request button is shown."""
        response = Client().get(reverse("firmwares:chipset_list"), {"q": NOTHING})

        assert "chipset" in cta_kinds(response)

    def test_a_brand_page_offers_a_model_request(self, catalog):
        """No model of the brand matches: the model request button is shown."""
        response = Client().get(
            reverse("firmwares:brand_detail", kwargs={"slug": catalog.brand.slug}),
            {"q": NOTHING},
        )

        assert "model" in cta_kinds(response)

    def test_firmware_browse_offers_firmware_and_model_requests(self):
        """No firmware matches: both the firmware and the model request show."""
        response = Client().get(reverse("firmwares:browse"), {"q": NOTHING})

        assert {"firmware", "model"} <= cta_kinds(response)

    def test_the_central_search_offers_every_kind_of_request(self):
        """The central search page offers all five requests."""
        response = Client().get(reverse("search:results"), {"q": NOTHING})

        assert {"brand", "model", "variant", "chipset", "firmware"} <= cta_kinds(
            response
        )

    def test_the_firmware_search_page_offers_a_model_request(self):
        """The firmware search page offers a model request."""
        response = Client().get(reverse("firmwares:universal_search"), {"q": NOTHING})

        assert "model" in cta_kinds(response)

    def test_a_model_page_offers_variant_and_firmware_requests(self, catalog):
        """A model page always offers the variant and firmware requests."""
        response = Client().get(model_url(catalog))

        assert {"variant", "firmware"} <= cta_kinds(response)

    def test_a_model_page_without_variants_still_offers_a_variant_request(
        self, catalog
    ):
        """Even with no variants listed, a variant can be requested."""
        Variant.objects.filter(model=catalog.model).delete()

        response = Client().get(model_url(catalog))

        assert "variant" in cta_kinds(response)

    def test_signed_out_visitors_are_sent_to_sign_in_first(self):
        """The button leads signed-out visitors to sign in, keeping the query."""
        response = Client().get(reverse("firmwares:brand_list"), {"q": NOTHING})

        body = response.content.decode()
        assert "/login" in body
        assert NOTHING in body
