from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.firmwares import download_service
from apps.firmwares.models import (
    Brand,
    EngineeringFirmware,
    Model,
    OfficialFirmware,
)
from apps.shop import services
from apps.shop.models import (
    Order,
    PackageQuotaOverride,
    PackageTier,
    Product,
    UserPackage,
)

PASSWORD = "Pass-12345!"


class EntitlementTestCase(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="buyer",
            email="buyer@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now(),
        )
        self.other = user_model.objects.create_user(
            username="other_buyer",
            email="other-buyer@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now(),
        )
        self.free = PackageTier.objects.create(
            name="Free",
            slug="free",
            is_default=True,
            daily_download_limit=2,
            monthly_download_limit=5,
            daily_bandwidth_mb=100,
            monthly_bandwidth_mb=-1,
        )
        self.pro = PackageTier.objects.create(
            name="Pro",
            slug="pro",
            tier_level="pro",
            price_monthly=Decimal("9.00"),
            price_yearly=Decimal("90.00"),
            access_engineering=True,
            ad_free=True,
            is_featured=True,
        )
        self.product = Product.objects.create(
            name="Pro package",
            slug="pro-package",
            product_type="package",
            price=Decimal("9.00"),
            package_tier=self.pro,
        )

    def login(self, user=None) -> Client:
        client = Client()
        client.force_login(user or self.user)
        return client


class EntitlementSummaryTests(EntitlementTestCase):
    def test_summary_uses_the_default_tier_without_writing_rows(self):
        summary = services.entitlement_summary(self.user)

        self.assertEqual(summary.tier, self.free)
        self.assertTrue(summary.is_default_tier)
        self.assertFalse(UserPackage.objects.exists())
        by_label = {meter.label: meter for meter in summary.meters}
        self.assertEqual(by_label["Downloads today"].limit, 2)
        self.assertTrue(by_label["Bandwidth this month"].unlimited)
        self.assertEqual(by_label["Bandwidth this month"].percent, 0)

    def test_summary_reports_usage_percent_and_text(self):
        package = services.activate_package(self.user, self.free)
        UserPackage.objects.filter(pk=package.pk).update(
            downloads_today=1, last_download_at=timezone.now()
        )

        meter = services.entitlement_summary(self.user).meters[0]

        self.assertEqual((meter.used, meter.percent), (1, 50))
        self.assertEqual(meter.text, "1 of 2")

    def test_yesterdays_counters_do_not_count_today_and_nothing_is_saved(self):
        package = services.activate_package(self.user, self.free)
        UserPackage.objects.filter(pk=package.pk).update(
            downloads_today=2, last_download_at=timezone.now() - timedelta(days=1)
        )

        meter = services.entitlement_summary(self.user).meters[0]

        self.assertEqual(meter.used, 0)
        package.refresh_from_db()
        self.assertEqual(package.downloads_today, 2)

    def test_staff_override_changes_the_displayed_limit(self):
        PackageQuotaOverride.objects.create(user=self.user, daily_download_limit=10)

        meter = services.entitlement_summary(self.user).meters[0]

        self.assertEqual(meter.limit, 10)

    def test_category_access_follows_the_tier(self):
        services.activate_package(self.user, self.pro)

        access = dict(services.entitlement_summary(self.user).type_access)

        self.assertTrue(access["Engineering"])
        self.assertFalse(access["Modified"])


class ShopPagesTests(EntitlementTestCase):
    def test_pricing_shows_features_current_package_and_the_payment_notice(self):
        services.activate_package(self.user, self.pro)

        response = self.login().get(reverse("shop:pricing"))

        self.assertContains(response, "Online payments are not available")
        self.assertContains(response, "Ad-free downloads")
        self.assertContains(response, "Your package")
        self.assertContains(response, "Request this package")
        self.assertContains(response, "Included with every account")
        self.assertContains(
            response, reverse("shop:product_detail", args=["pro-package"])
        )

    def test_pricing_is_public_and_marks_no_package_for_anonymous_visitors(self):
        response = self.client.get(reverse("shop:pricing"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Your package")

    def test_product_detail_has_a_csrf_protected_request_form(self):
        response = self.login().get(
            reverse("shop:product_detail", args=["pro-package"])
        )

        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, reverse("shop:place_order", args=["pro-package"]))
        self.assertContains(response, "Nothing is charged")

    def test_csrf_is_enforced_when_requesting_an_order(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(reverse("shop:place_order", args=["pro-package"]))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Order.objects.exists())

    def test_unverified_users_see_why_they_cannot_order(self):
        user = get_user_model().objects.create_user(
            username="unverified_buyer",
            email="unverified-buyer@example.com",
            password=PASSWORD,
        )

        response = self.login(user).get(
            reverse("shop:product_detail", args=["pro-package"])
        )

        self.assertContains(
            response, "Verify your email address before placing an order"
        )
        self.assertNotContains(
            response, reverse("shop:place_order", args=["pro-package"])
        )

    def test_anonymous_visitors_are_asked_to_sign_in(self):
        response = self.client.get(reverse("shop:product_detail", args=["pro-package"]))

        self.assertContains(response, "to request this order")

    def test_my_package_page_shows_meters_access_and_a_not_enforced_note(self):
        response = self.login().get(reverse("shop:my_subscriptions"))

        self.assertContains(response, "Downloads today")
        self.assertContains(response, "<meter", html=False)
        self.assertContains(response, "Firmware categories")
        self.assertContains(response, "not applied to downloads")

    @override_settings(SHOP_ENTITLEMENTS_ENFORCED=True)
    def test_my_package_page_drops_the_note_when_enforcement_is_on(self):
        response = self.login().get(reverse("shop:my_subscriptions"))

        self.assertNotContains(response, "not applied to downloads")

    def test_order_detail_shows_progress_for_the_owner_only(self):
        order = services.create_order(self.user, self.product, "monthly")

        mine = self.login().get(reverse("shop:order_detail", args=[order.pk]))
        theirs = self.login(self.other).get(
            reverse("shop:order_detail", args=[order.pk])
        )
        anonymous = self.client.get(reverse("shop:order_detail", args=[order.pk]))

        self.assertContains(mine, f"#{order.pk}")
        self.assertContains(mine, "Waiting for staff to confirm your payment")
        self.assertContains(mine, "Online payments are not available")
        self.assertEqual(theirs.status_code, 404)
        self.assertEqual(anonymous.status_code, 302)

    def test_paid_order_detail_shows_confirmation(self):
        staff = get_user_model().objects.create_user(
            username="shop_ui_staff",
            email="shop-ui-staff@example.com",
            password=PASSWORD,
        )
        order = services.create_order(self.user, self.product, "monthly")
        services.mark_order_paid(order.pk, staff_user=staff, reference="REF-1")

        response = self.login().get(reverse("shop:order_detail", args=[order.pk]))

        self.assertContains(response, "Payment confirmed")
        self.assertContains(response, "REF-1")

    def test_order_history_links_to_each_order(self):
        order = services.create_order(self.user, self.product, "monthly")

        response = self.login().get(reverse("shop:order_history"))

        self.assertContains(response, reverse("shop:order_detail", args=[order.pk]))


class DashboardAndFirmwareSeamTests(EntitlementTestCase):
    def test_account_dashboard_links_the_shop_package_and_pending_orders(self):
        services.create_order(self.user, self.product, "monthly")

        response = self.login().get(reverse("users:dashboard"))

        self.assertContains(response, reverse("shop:my_subscriptions"))
        self.assertContains(response, "Download package")
        self.assertContains(response, "1 order(s) awaiting payment confirmation")

    def test_firmware_page_shows_the_visitors_allowance(self):
        brand = Brand.objects.create(name="Seam Brand", slug="seam-brand")
        model = Model.objects.create(brand=brand, name="S1", slug="s1")
        firmware = OfficialFirmware.objects.create(
            original_file_name="seam.zip",
            stored_file_path="/firmware/seam.zip",
            brand=brand,
            model=model,
            is_verified=True,
            is_active=True,
        )
        url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": str(firmware.pk)},
        )

        signed_in = self.login().get(url)
        anonymous = self.client.get(url)

        self.assertContains(signed_in, "Your download allowance")
        self.assertContains(signed_in, "Compare packages")
        self.assertNotContains(anonymous, "Your download allowance")


class DownloadGateSeamTests(EntitlementTestCase):
    def request_for(self, user):
        request = RequestFactory().get("/")
        request.user = user
        return request

    def firmware(self, model_class=OfficialFirmware, size=1024 * 1024):
        return model_class.objects.create(
            original_file_name="gate.zip",
            stored_file_path="/firmware/gate.zip",
            file_size=size,
            is_verified=True,
            is_active=True,
        )

    def test_enforcement_is_off_by_default_so_behaviour_is_unchanged(self):
        firmware = self.firmware(EngineeringFirmware)

        allowed, reason = download_service.check_download_allowed(
            self.request_for(self.user), firmware
        )

        self.assertEqual((allowed, reason), (True, ""))
        download_service.record_entitlement_download(
            self.request_for(self.user), firmware
        )
        self.assertFalse(UserPackage.objects.exists())

    @override_settings(SHOP_ENTITLEMENTS_ENFORCED=True)
    def test_tier_without_engineering_access_is_refused(self):
        firmware = self.firmware(EngineeringFirmware)

        allowed, reason = download_service.check_download_allowed(
            self.request_for(self.user), firmware
        )

        self.assertFalse(allowed)
        self.assertIn("does not include this firmware category", reason)

    @override_settings(SHOP_ENTITLEMENTS_ENFORCED=True)
    def test_package_with_access_is_allowed_and_usage_is_recorded(self):
        services.activate_package(self.user, self.pro)
        firmware = self.firmware(EngineeringFirmware, size=3 * 1024 * 1024)
        request = self.request_for(self.user)

        self.assertEqual(
            download_service.check_download_allowed(request, firmware), (True, "")
        )
        download_service.record_entitlement_download(request, firmware)

        package = services.get_active_package(self.user)
        if package is None:
            self.fail("The user should have an active package.")
        self.assertEqual(package.downloads_today, 1)
        self.assertEqual(package.bandwidth_today_mb, 3)

    @override_settings(SHOP_ENTITLEMENTS_ENFORCED=True)
    def test_daily_quota_is_enforced_and_reported(self):
        firmware = self.firmware()
        request = self.request_for(self.user)
        for _ in range(2):
            download_service.record_entitlement_download(request, firmware)

        allowed, reason = download_service.check_download_allowed(request, firmware)

        self.assertFalse(allowed)
        self.assertEqual(reason, "Daily download limit reached.")

    @override_settings(SHOP_ENTITLEMENTS_ENFORCED=True)
    def test_anonymous_visitors_keep_the_existing_ip_policy(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.request_for(AnonymousUser())

        allowed, _ = download_service.check_download_allowed(request, self.firmware())

        self.assertTrue(allowed)

    @override_settings(SHOP_ENTITLEMENTS_ENFORCED=True)
    def test_refusal_reaches_the_download_page_as_a_message(self):
        firmware = self.firmware(EngineeringFirmware)
        url = reverse(
            "firmwares:firmware_download",
            kwargs={"firmware_type": "engineering", "firmware_id": str(firmware.pk)},
        )

        response = self.login().get(url, follow=True)

        self.assertContains(response, "does not include this firmware category")
