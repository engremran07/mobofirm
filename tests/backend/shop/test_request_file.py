"""Requesting a paid firmware file from its page: /shop/request-file/<type>/<uuid>/."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.shop import entitlements, services
from apps.shop.models import Order

pytestmark = pytest.mark.django_db


def request_url(firmware, firmware_type: str = "official") -> str:
    return reverse(
        "shop:request_file",
        kwargs={"firmware_type": firmware_type, "firmware_id": firmware.pk},
    )


@pytest.fixture
def signed_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


class TestRequestFileView:
    def test_post_creates_an_order_and_says_staff_will_confirm(
        self, signed_in, user, make_firmware
    ):
        firmware = make_firmware("paid", "7.50")

        response = signed_in.post(request_url(firmware), follow=True)

        order = Order.objects.get()
        assert order.user == user
        assert order.total == Decimal("7.50")
        assert response.redirect_chain[-1][0] == reverse(
            "shop:order_detail", kwargs={"pk": order.pk}
        )
        assert "our team will confirm payment" in response.content.decode()
        assert "Online payments are not available" in response.content.decode()
        assert firmware.original_file_name in response.content.decode()

    def test_posting_twice_keeps_a_single_order(self, signed_in, make_firmware):
        firmware = make_firmware("paid", "7.50")

        signed_in.post(request_url(firmware))
        signed_in.post(request_url(firmware))

        assert Order.objects.count() == 1

    def test_get_is_not_allowed_and_csrf_is_enforced(self, user, make_firmware):
        firmware = make_firmware("paid", "7.50")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)

        assert client.get(request_url(firmware)).status_code == 405
        assert client.post(request_url(firmware)).status_code == 403
        assert not Order.objects.exists()

    def test_anonymous_visitors_are_sent_to_sign_in(self, client, make_firmware):
        response = client.post(request_url(make_firmware("paid", "7.50")))

        assert response.status_code == 302
        assert "login" in response["Location"]
        assert not Order.objects.exists()

    def test_unverified_users_cannot_request(self, make_user, make_firmware):
        client = Client()
        client.force_login(make_user("unverified", verified=False))

        client.post(request_url(make_firmware("paid", "7.50")))

        assert not Order.objects.exists()

    def test_rejections_redirect_back_to_the_file_with_a_message(
        self, signed_in, make_firmware
    ):
        free = make_firmware("free")

        response = signed_in.post(request_url(free), follow=True)

        assert response.redirect_chain[-1][0] == reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": free.pk},
        )
        assert "not sold" in response.content.decode()
        assert not Order.objects.exists()

    @pytest.mark.parametrize("hidden", [{"is_verified": False}, {"is_active": False}])
    def test_unpublished_files_are_a_404(self, signed_in, make_firmware, hidden):
        firmware = make_firmware("paid", "7.50", **hidden)

        assert signed_in.post(request_url(firmware)).status_code == 404

    def test_unknown_type_is_a_404(self, signed_in, make_firmware):
        firmware = make_firmware("paid", "7.50")

        assert signed_in.post(request_url(firmware, "nonsense")).status_code == 404


class TestFirmwarePageAllowance:
    def firmware_page(self, client: Client, firmware) -> str:
        url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
        )
        return client.get(url).content.decode()

    def test_paid_file_shows_the_price_and_the_request_form(
        self, signed_in, default_tier, make_firmware
    ):
        firmware = make_firmware("paid", "7.50")

        page = self.firmware_page(signed_in, firmware)

        assert "7.50 USD" in page
        assert f'action="{request_url(firmware)}"' in page
        assert "Request this file" in page

    def test_owned_file_no_longer_offers_a_purchase(
        self, signed_in, user, staff, default_tier, make_firmware
    ):
        firmware = make_firmware("paid", "7.50")
        order = entitlements.request_purchase(user, firmware)
        services.mark_order_paid(order.pk, staff_user=staff)

        page = self.firmware_page(signed_in, firmware)

        assert "You bought this file" in page
        assert "Request this file" not in page

    def test_featured_file_tells_non_members_it_is_for_package_holders(
        self, signed_in, default_tier, make_firmware
    ):
        page = self.firmware_page(signed_in, make_firmware("featured"))

        assert "Featured files need a package that includes them" in page
