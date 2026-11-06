from __future__ import annotations

from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.shop import services
from apps.shop.models import Order, PackageTier, Product, UserPackage

DIALOG = {"_form_submitted": "on"}


class ShopAdminActionTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="shop-admin-customer", email="shop-admin-customer@example.test"
        )
        self.admin_user = user_model.objects.create_superuser(
            username="shop-admin-root", email="shop-admin-root@example.test"
        )
        self.pro = PackageTier.objects.create(
            name="Pro",
            slug="pro-admin",
            tier_level="pro",
            price_monthly=Decimal("9.00"),
        )
        self.product = Product.objects.create(
            name="Pro package",
            slug="pro-package-admin",
            product_type="package",
            price=Decimal("9.00"),
            package_tier=self.pro,
        )
        self.order = services.create_order(self.customer, self.product)
        self.client = Client()
        self.client.force_login(self.admin_user)

    def order_url(self, name: str) -> str:
        return reverse(f"admin:shop_order_{name}", args=[self.order.pk])

    def test_mark_paid_dialog_provisions_the_order_once(self) -> None:
        opened = self.client.get(self.order_url("mark_order_paid"))
        first = self.client.post(
            self.order_url("mark_order_paid"), {**DIALOG, "reference": "RCPT-42"}
        )
        second = self.client.post(self.order_url("mark_order_paid"), DIALOG)

        self.assertEqual(opened.status_code, 200)
        self.assertIn("HX-Redirect", first.headers)
        self.assertIn("HX-Redirect", second.headers)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.payment_reference, "RCPT-42")
        self.assertEqual(self.order.processed_by, self.admin_user)
        self.assertEqual(UserPackage.objects.filter(user=self.customer).count(), 1)

    def test_cancelled_orders_cannot_be_paid(self) -> None:
        self.client.post(self.order_url("cancel_order"), DIALOG)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)

        self.client.post(self.order_url("mark_order_paid"), DIALOG)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELLED)
        self.assertFalse(UserPackage.objects.exists())

    def test_paid_orders_cannot_be_cancelled(self) -> None:
        self.client.post(self.order_url("mark_order_paid"), DIALOG)

        self.client.post(self.order_url("cancel_order"), DIALOG)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_bulk_actions_still_work(self) -> None:
        self.client.post(
            reverse("admin:shop_order_changelist"),
            {"action": "mark_paid", "_selected_action": [self.order.pk]},
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_staff_without_change_permission_cannot_run_order_actions(self) -> None:
        viewer = get_user_model().objects.create_user(
            username="shop-viewer", email="shop-viewer@example.test", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_order"))
        client = Client()
        client.force_login(viewer)

        dialog = client.post(self.order_url("mark_order_paid"), DIALOG)
        bulk = client.post(
            reverse("admin:shop_order_changelist"),
            {"action": "mark_paid", "_selected_action": [self.order.pk]},
        )

        self.assertEqual(dialog.status_code, 403)
        self.assertEqual(bulk.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_orders_cannot_be_added_by_hand(self) -> None:
        response = self.client.get(reverse("admin:shop_order_add"))

        self.assertEqual(response.status_code, 403)

    def test_cancel_package_dialog_and_bulk_action(self) -> None:
        package = services.activate_package(self.customer, self.pro)
        url = reverse("admin:shop_userpackage_cancel_package", args=[package.pk])

        self.client.post(url, DIALOG)
        package.refresh_from_db()
        self.assertEqual(package.status, UserPackage.Status.CANCELLED)
        self.assertIsNotNone(package.cancelled_at)

        again = services.activate_package(self.customer, self.pro)
        self.client.post(
            reverse("admin:shop_userpackage_changelist"),
            {"action": "cancel_selected", "_selected_action": [again.pk]},
        )
        again.refresh_from_db()
        self.assertEqual(again.status, UserPackage.Status.CANCELLED)

    def test_every_shop_changelist_and_add_form_render(self) -> None:
        request = RequestFactory().get("/")
        request.user = self.admin_user
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label != "shop":
                continue
            name = model._meta.model_name
            with self.subTest(model=model._meta.label, page="changelist"):
                response = self.client.get(reverse(f"admin:shop_{name}_changelist"))
                self.assertEqual(response.status_code, 200)
            if model_admin.has_add_permission(request):
                with self.subTest(model=model._meta.label, page="add"):
                    response = self.client.get(reverse(f"admin:shop_{name}_add"))
                    self.assertEqual(response.status_code, 200)
        detail = self.client.get(
            reverse("admin:shop_order_change", args=[self.order.pk])
        )
        self.assertEqual(detail.status_code, 200)
