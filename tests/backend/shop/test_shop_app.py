from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.shop import services
from apps.shop import urls as shop_urls
from apps.shop.models import (
    Order,
    PackageQuotaOverride,
    PackageTier,
    Product,
    Subscription,
    SubscriptionPlan,
    UserPackage,
)
from apps.shop.tasks import expire_packages

PASSWORD = "Pass-12345!"


class ShopTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="shopper",
            email="shopper@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now(),
        )
        self.unverified = user_model.objects.create_user(
            username="unverified_shopper",
            email="unverified-shopper@example.com",
            password=PASSWORD,
        )
        self.staff = user_model.objects.create_user(
            username="shop_staff",
            email="shop-staff@example.com",
            password=PASSWORD,
            is_staff=True,
            is_superuser=True,
        )
        self.free = PackageTier.objects.create(
            name="Free",
            slug="free",
            is_default=True,
            daily_download_limit=2,
            monthly_download_limit=3,
            daily_bandwidth_mb=100,
            max_file_size_mb=50,
        )
        self.pro = PackageTier.objects.create(
            name="Pro",
            slug="pro",
            tier_level="pro",
            price_monthly=Decimal("9.00"),
            daily_download_limit=-1,
            monthly_download_limit=-1,
            daily_bandwidth_mb=-1,
            monthly_bandwidth_mb=-1,
            max_file_size_mb=-1,
            access_engineering=True,
        )
        self.pro_product = Product.objects.create(
            name="Pro package",
            slug="pro-package",
            product_type="package",
            price=Decimal("9.00"),
            package_tier=self.pro,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Plus", slug="plus", price_monthly=Decimal("5.00")
        )
        self.sub_product = Product.objects.create(
            name="Plus plan",
            slug="plus-plan",
            product_type="subscription",
            price=Decimal("5.00"),
            subscription_plan=self.plan,
        )


class OnlinePaymentsDisabledTests(ShopTestCase):
    def test_online_payments_are_explicitly_disabled_and_visible_in_the_ui(self):
        self.assertFalse(services.ONLINE_PAYMENTS_ENABLED)

        for name, kwargs in (
            ("shop:pricing", {}),
            ("shop:product_list", {}),
            ("shop:product_detail", {"slug": "pro-package"}),
        ):
            with self.subTest(route=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertContains(response, "Online payments are not available")

    def test_no_payment_provider_routes_remain(self):
        names = {route.name for route in shop_urls.urlpatterns}

        self.assertFalse({name for name in names if name and "stripe" in name})
        self.assertFalse({name for name in names if name and "webhook" in name})


class OrderFlowTests(ShopTestCase):
    def test_create_order_is_pending_and_priced_from_the_product(self):
        order = services.create_order(self.user, self.pro_product, "yearly")

        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.total, Decimal("9.00"))
        self.assertEqual(order.items.get().billing_cycle, "yearly")
        self.assertFalse(UserPackage.objects.filter(user=self.user).exists())

    def test_repeated_requests_reuse_the_pending_order(self):
        first = services.create_order(self.user, self.pro_product, "monthly")
        again = services.create_order(self.user, self.pro_product, "monthly")
        yearly = services.create_order(self.user, self.pro_product, "yearly")

        self.assertEqual(first.pk, again.pk)
        self.assertNotEqual(first.pk, yearly.pk)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 2)

    def test_inactive_product_and_bad_cycle_are_rejected(self):
        self.pro_product.is_active = False
        self.pro_product.save()
        with self.assertRaises(services.OrderError):
            services.create_order(self.user, self.pro_product)
        with self.assertRaises(services.OrderError):
            services.create_order(self.user, self.sub_product, "weekly")

    def test_mark_paid_provisions_once_and_is_idempotent(self):
        order = services.create_order(self.user, self.pro_product)

        services.mark_order_paid(order.pk, staff_user=self.staff, reference="BANK-1")
        services.mark_order_paid(order.pk, staff_user=self.staff, reference="BANK-2")

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.payment_reference, "BANK-1")
        self.assertEqual(order.processed_by, self.staff)
        package = UserPackage.objects.get(user=self.user)
        self.assertEqual(package.package, self.pro)
        self.assertEqual(package.status, UserPackage.Status.ACTIVE)
        self.assertEqual(UserPackage.objects.filter(user=self.user).count(), 1)

    def test_subscription_product_creates_a_subscription(self):
        order = services.create_order(self.user, self.sub_product, "monthly")

        services.mark_order_paid(order.pk, staff_user=self.staff)

        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.plan, self.plan)
        self.assertIsNotNone(subscription.expires_at)

    def test_cancelled_orders_cannot_be_paid_and_paid_orders_cannot_be_cancelled(self):
        cancelled = services.create_order(self.user, self.pro_product)
        services.cancel_order(cancelled.pk, staff_user=self.staff)
        with self.assertRaises(services.OrderError):
            services.mark_order_paid(cancelled.pk, staff_user=self.staff)

        paid = services.create_order(self.user, self.sub_product)
        services.mark_order_paid(paid.pk, staff_user=self.staff)
        with self.assertRaises(services.OrderError):
            services.cancel_order(paid.pk, staff_user=self.staff)

    def test_activating_a_package_expires_the_previous_one(self):
        first = services.activate_package(self.user, self.free)
        services.activate_package(self.user, self.pro)

        first.refresh_from_db()
        self.assertEqual(first.status, UserPackage.Status.EXPIRED)
        self.assertEqual(
            UserPackage.objects.filter(user=self.user, status="active").count(), 1
        )

    def test_database_allows_only_one_live_package_per_user(self):
        UserPackage.objects.create(user=self.user, package=self.free)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserPackage.objects.create(user=self.user, package=self.pro)

    def test_package_products_need_a_tier(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(
                name="Broken", slug="broken", product_type="package", price=1
            )

    def test_expiry_task_expires_overdue_packages_and_subscriptions(self):
        past = timezone.now() - timedelta(days=1)
        package = UserPackage.objects.create(
            user=self.user, package=self.pro, expires_at=past
        )
        subscription = Subscription.objects.create(
            user=self.user, plan=self.plan, expires_at=past
        )
        UserPackage.objects.create(user=self.staff, package=self.pro)

        result = expire_packages()

        package.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(result, {"packages": 1, "subscriptions": 1})
        self.assertEqual(package.status, UserPackage.Status.EXPIRED)
        self.assertEqual(subscription.status, Subscription.Status.EXPIRED)
        self.assertEqual(expire_packages(), {"packages": 0, "subscriptions": 0})


class QuotaTests(ShopTestCase):
    def test_default_tier_is_assigned_and_limits_are_enforced(self):
        self.assertTrue(services.check_download_quota(self.user, size_mb=10).allowed)
        services.record_download(self.user, size_mb=10)
        services.record_download(self.user, size_mb=10)

        decision = services.check_download_quota(self.user, size_mb=10)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "Daily download limit reached.")

    def test_file_size_and_bandwidth_limits(self):
        self.assertFalse(services.check_download_quota(self.user, size_mb=51).allowed)
        services.record_download(self.user, size_mb=90)

        decision = services.check_download_quota(self.user, size_mb=20)

        self.assertEqual(decision.reason, "Daily bandwidth limit reached.")

    def test_unlimited_tier_and_user_overrides(self):
        services.activate_package(self.user, self.pro)
        for _ in range(5):
            services.record_download(self.user, size_mb=1000)
        self.assertTrue(services.check_download_quota(self.user, size_mb=5000).allowed)

        other = get_user_model().objects.create_user(
            username="override_user", email="override@example.com", password=PASSWORD
        )
        PackageQuotaOverride.objects.create(
            user=other, daily_download_limit=10, monthly_download_limit=10
        )
        for _ in range(3):
            services.record_download(other)
        self.assertTrue(services.check_download_quota(other).allowed)

    def test_expired_override_is_ignored(self):
        PackageQuotaOverride.objects.create(
            user=self.user,
            daily_download_limit=100,
            expires_at=timezone.now() - timedelta(days=1),
        )
        services.record_download(self.user)
        services.record_download(self.user)

        self.assertFalse(services.check_download_quota(self.user).allowed)

    def test_usage_windows_roll_over(self):
        services.record_download(self.user)
        services.record_download(self.user)
        UserPackage.objects.filter(user=self.user).update(
            last_download_at=timezone.now() - timedelta(days=2)
        )

        self.assertTrue(services.check_download_quota(self.user).allowed)

    def test_firmware_type_access_follows_the_tier(self):
        self.assertTrue(services.firmware_type_allowed(self.user, "official"))
        self.assertFalse(services.firmware_type_allowed(self.user, "engineering"))
        services.activate_package(self.user, self.pro)
        self.assertTrue(services.firmware_type_allowed(self.user, "engineering"))
        self.assertFalse(services.firmware_type_allowed(self.user, "unknown"))

    def test_no_default_tier_means_no_downloads(self):
        PackageTier.objects.filter(pk=self.free.pk).update(is_default=False)

        self.assertFalse(services.check_download_quota(self.user).allowed)


class ShopViewTests(ShopTestCase):
    def test_public_pages_render(self):
        for name, kwargs in (
            ("shop:pricing", {}),
            ("shop:product_list", {}),
            ("shop:product_detail", {"slug": "pro-package"}),
        ):
            with self.subTest(route=name):
                response = self.client.get(reverse(name, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)

    def test_inactive_product_is_hidden(self):
        self.pro_product.is_active = False
        self.pro_product.save()

        response = self.client.get(
            reverse("shop:product_detail", kwargs={"slug": "pro-package"})
        )

        self.assertEqual(response.status_code, 404)

    def test_account_pages_require_login(self):
        for name in ("shop:my_subscriptions", "shop:order_history"):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_placing_an_order_requires_post_login_verified_email_and_csrf(self):
        url = reverse("shop:place_order", kwargs={"slug": "pro-package"})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(Order.objects.count(), 0)

        self.client.force_login(self.unverified)
        self.client.post(url)
        self.assertEqual(Order.objects.count(), 0)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url).status_code, 403)

        self.client.force_login(self.user)
        response = self.client.post(url, {"billing_cycle": "monthly"})
        self.assertRedirects(response, reverse("shop:order_history"))
        order = Order.objects.get()
        self.assertEqual((order.user, order.status), (self.user, Order.Status.PENDING))

    def test_order_history_only_shows_own_orders(self):
        mine = services.create_order(self.user, self.pro_product)
        services.create_order(self.staff, self.sub_product)
        self.client.force_login(self.user)

        response = self.client.get(reverse("shop:order_history"))

        self.assertContains(response, f"#{mine.pk}")
        self.assertEqual(len(response.context["orders"]), 1)

    def test_my_subscriptions_shows_package_state(self):
        services.activate_package(self.user, self.pro)
        self.client.force_login(self.user)

        response = self.client.get(reverse("shop:my_subscriptions"))

        self.assertContains(response, "Pro")


class ShopAdminTests(ShopTestCase):
    def test_every_model_is_registered(self):
        for model in (
            SubscriptionPlan,
            PackageTier,
            UserPackage,
            PackageQuotaOverride,
            Product,
            Subscription,
            Order,
        ):
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)

    def test_mark_paid_admin_action_provisions_and_blocks_non_permitted_staff(self):
        order = services.create_order(self.user, self.pro_product)
        self.client.force_login(self.staff)
        url = reverse("admin:shop_order_changelist")

        response = self.client.post(
            url, {"action": "mark_paid", "_selected_action": [order.pk]}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(UserPackage.objects.filter(user=self.user).exists())

        limited = get_user_model().objects.create_user(
            username="limited_staff",
            email="limited-staff@example.com",
            password=PASSWORD,
            is_staff=True,
        )
        second = services.create_order(self.user, self.sub_product)
        self.client.force_login(limited)
        self.client.post(url, {"action": "mark_paid", "_selected_action": [second.pk]})
        second.refresh_from_db()
        self.assertEqual(second.status, Order.Status.PENDING)
