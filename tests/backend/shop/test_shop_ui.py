"""Public shop pages: pricing summary and comparison, package page, orders, seams."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.shop import services
from apps.shop.models import PackageTier, SizeUnit, WindowKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def pricing_catalog(db) -> dict[str, PackageTier]:
    free = PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.DAY,
        limit_files=10,
        limit_size=3,
        device_limit=3,
    )
    premium = PackageTier.objects.create(
        name="Premium",
        slug="premium",
        price=Decimal(9),
        credit_price_override=300,
        duration_days=30,
        window=WindowKind.PACKAGE,
        limit_files=30,
        limit_size=20,
        size_unit=SizeUnit.GB,
        device_limit=5,
        applies_to_featured=True,
        ad_free=True,
        is_popular=True,
        sort_order=1,
    )
    gold = PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal(29),
        duration_days=365,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured=True,
        applies_to_paid=True,
        paid_discount_percent=25,
        sort_order=2,
    )
    return {"free": free, "premium": premium, "gold": gold}


class TestPricingPage:
    def test_cards_use_the_package_summary(self, client, pricing_catalog):
        text = client.get(reverse("shop:pricing")).content.decode()

        for package in pricing_catalog.values():
            assert package.summary() in text
        assert (
            "30 files / 20 GB per 30 days · free + featured · 5 devices · $9/mo" in text
        )
        assert "Popular" in text

    def test_comparison_table_compares_quota_devices_and_featured_access(
        self, client, pricing_catalog
    ):
        response = client.get(reverse("shop:pricing"))

        text = response.content.decode()
        assert "Compare packages" in text
        for label in ("Download quota", "Devices", "Featured files", "Paid files"):
            assert label in text
        rows = dict(response.context["rows"])
        assert rows["Featured files"] == [False, True, True]
        assert rows["Devices"] == ["3 devices", "5 devices", "unlimited devices"]
        assert rows["Paid files"] == [False, False, "25% off"]
        assert rows["Ad-free downloads"] == [False, True, False]

    def test_current_plan_is_marked_for_the_holder(self, client, user, pricing_catalog):
        services.activate_package(user, pricing_catalog["premium"])
        client.force_login(user)

        response = client.get(reverse("shop:pricing"))

        assert response.context["current_package_id"] == pricing_catalog["premium"].pk
        assert response.content.decode().count("Current plan") >= 2
        assert "Renew" in response.content.decode()

    def test_everyone_without_a_package_is_on_the_free_plan(
        self, client, user, pricing_catalog
    ):
        client.force_login(user)

        response = client.get(reverse("shop:pricing"))

        assert response.context["current_package_id"] == pricing_catalog["free"].pk

    def test_anonymous_visitors_see_the_page_and_the_payment_notice(
        self, client, pricing_catalog
    ):
        response = client.get(reverse("shop:pricing"))

        assert response.status_code == 200
        assert "Online payments are not available" in response.content.decode()

    def test_hidden_packages_stay_off_the_page(self, client, pricing_catalog):
        PackageTier.objects.filter(slug="gold").update(is_public=False)

        text = client.get(reverse("shop:pricing")).content.decode()

        assert "Gold" not in text

    def test_explains_free_featured_and_paid_files(self, client):
        text = client.get(reverse("shop:pricing")).content.decode()

        for phrase in ("Free files", "Featured files", "Paid files", "staff"):
            assert phrase in text
        assert "No packages are available yet" in text


class TestPackagePage:
    def test_has_a_csrf_protected_request_form(self, client, user, pricing_catalog):
        client.force_login(user)

        text = client.get(
            reverse("shop:package_detail", kwargs={"slug": "premium"})
        ).content.decode()

        assert "csrfmiddlewaretoken" in text
        assert reverse("shop:place_order", kwargs={"slug": "premium"}) in text
        assert "Nothing is charged" in text

    def test_unverified_users_see_why_they_cannot_order(
        self, client, make_user, pricing_catalog
    ):
        client.force_login(make_user("unverified", verified=False))

        text = client.get(
            reverse("shop:package_detail", kwargs={"slug": "premium"})
        ).content.decode()

        assert "Verify your email address before placing an order" in text
        assert reverse("shop:place_order", kwargs={"slug": "premium"}) not in text

    def test_anonymous_visitors_are_asked_to_sign_in(self, client, pricing_catalog):
        text = client.get(
            reverse("shop:package_detail", kwargs={"slug": "premium"})
        ).content.decode()

        assert "to get this package" in text

    def test_the_default_package_has_nothing_to_order(self, client, pricing_catalog):
        text = client.get(
            reverse("shop:package_detail", kwargs={"slug": "free"})
        ).content.decode()

        assert "included with every account" in text
        assert "place_order" not in text

    def test_credits_are_hidden_while_the_wallet_is_unavailable(
        self, client, user, pricing_catalog
    ):
        client.force_login(user)

        text = client.get(
            reverse("shop:package_detail", kwargs={"slug": "premium"})
        ).content.decode()

        assert "Pay 300 credits" not in text


class TestAccountPages:
    def test_my_package_shows_meters_devices_and_the_counting_rule(
        self, client, user, pricing_catalog
    ):
        client.force_login(user)

        text = client.get(reverse("shop:my_package")).content.decode()

        assert "New files today" in text
        assert "<meter" in text
        assert "Included with every account" in text
        assert "3 devices" in text
        assert "counts once" in text or "Each file counts once" in text

    def test_my_package_lists_perks_of_a_held_package(
        self, client, user, pricing_catalog
    ):
        services.activate_package(user, pricing_catalog["premium"])
        client.force_login(user)

        text = client.get(reverse("shop:my_package")).content.decode()

        assert "Ad-free downloads" in text
        assert "Premium" in text

    def test_order_detail_shows_progress_for_the_owner_only(
        self, client, user, other, pricing_catalog
    ):
        order = services.create_order(user, pricing_catalog["premium"])
        url = reverse("shop:order_detail", args=[order.pk])

        client.force_login(user)
        mine = client.get(url)
        client.force_login(other)
        theirs = client.get(url)
        anonymous = Client().get(url)

        text = mine.content.decode()
        assert f"#{order.pk}" in text
        assert "Waiting for staff to confirm your payment" in text
        assert "Package · 30 days" in text
        assert theirs.status_code == 404
        assert anonymous.status_code == 302

    def test_paid_order_detail_shows_confirmation(
        self, client, user, staff, pricing_catalog
    ):
        order = services.create_order(user, pricing_catalog["premium"])
        services.mark_order_paid(order.pk, staff_user=staff, reference="REF-1")
        client.force_login(user)

        text = client.get(
            reverse("shop:order_detail", args=[order.pk])
        ).content.decode()

        assert "Payment confirmed" in text
        assert "REF-1" in text

    def test_order_history_links_to_each_order(self, client, user, pricing_catalog):
        order = services.create_order(user, pricing_catalog["premium"])
        client.force_login(user)

        text = client.get(reverse("shop:order_history")).content.decode()

        assert reverse("shop:order_detail", args=[order.pk]) in text
        assert "Premium" in text


class TestSeams:
    def test_account_dashboard_links_the_shop_package_and_pending_orders(
        self, client, user, pricing_catalog
    ):
        services.create_order(user, pricing_catalog["premium"])
        client.force_login(user)

        text = client.get(reverse("users:dashboard")).content.decode()

        assert reverse("shop:my_package") in text
        assert "Download package" in text
        assert "1 order(s) awaiting payment confirmation" in text

    def test_firmware_page_shows_the_visitors_allowance(
        self, client, user, pricing_catalog, make_firmware
    ):
        firmware = make_firmware()
        url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": str(firmware.pk)},
        )
        client.force_login(user)

        signed_in = client.get(url).content.decode()
        anonymous = Client().get(url).content.decode()

        assert "Your download allowance" in signed_in
        assert "10 new files left today" in signed_in
        assert "Your download allowance" not in anonymous

    def test_firmware_page_shows_the_discounted_price_to_package_holders(
        self, client, user, pricing_catalog, make_firmware
    ):
        services.activate_package(user, pricing_catalog["gold"])
        firmware = make_firmware("paid", "10.00")
        url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": str(firmware.pk)},
        )
        client.force_login(user)

        text = client.get(url).content.decode()

        assert "7.50 USD" in text
        assert "list 10.00 USD" in text
