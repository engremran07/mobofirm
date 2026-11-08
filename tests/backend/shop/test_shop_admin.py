"""Unfold admin for the shop: one tabbed Package form, summaries, actions, orders."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.shop import services
from apps.shop.admin import PackageTierAdmin
from apps.shop.models import (
    Order,
    PackageQuotaOverride,
    PackageTier,
    UserPackage,
    WindowKind,
)

pytestmark = pytest.mark.django_db

DIALOG = {"_form_submitted": "on"}


def package_form(**overrides) -> dict[str, str]:
    """A valid add/change POST for the package form; ``None`` unchecks a box."""
    data: dict[str, str | None] = {
        "name": "Silver",
        "slug": "silver",
        "description": "",
        "price": "5",
        "allow_credits": "on",
        "duration_days": "30",
        "limit_devices": "on",
        "device_limit": "3",
        "is_public": "on",
        "is_active": "on",
        "sort_order": "0",
        "bandwidth_enabled": "on",
        "limit_files": "10",
        "limit_size": "0",
        "size_unit": "GB",
        "window": WindowKind.MONTH,
        "applies_to_free": "on",
        "paid_discount_percent": "0",
        "featured_blog_limit": "0",
        "extra_daily_files": "0",
        "extra_daily_size": "0",
        "fair_daily_files": "0",
        "fair_daily_size": "0",
        "fair_monthly_files": "0",
        "fair_monthly_size": "0",
        "max_concurrent_downloads": "0",
        "cooldown_seconds": "0",
        "resume_support": "on",
        "user_packages-TOTAL_FORMS": "0",
        "user_packages-INITIAL_FORMS": "0",
        "user_packages-MIN_NUM_FORMS": "0",
        "user_packages-MAX_NUM_FORMS": "0",
        "_save": "Save",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


@pytest.fixture
def root(client, staff):
    client.force_login(staff)
    return client


@pytest.fixture
def order(user, pro_tier):
    return services.create_order(user, pro_tier)


def order_url(order: Order, name: str) -> str:
    return reverse(f"admin:shop_order_{name}", args=[order.pk])


class TestPackageAdmin:
    def test_the_form_has_the_documented_tabs_and_a_preview(self, root, pro_tier):
        response = root.get(
            reverse("admin:shop_packagetier_change", args=[pro_tier.pk])
        )

        assert response.status_code == 200
        names = [str(name) for name, _ in PackageTierAdmin.fieldsets]
        assert names == [
            "Basic",
            "Bandwidth",
            "Item types",
            "Extra",
            "Fair usage",
            "Perks",
            "Preview",
        ]
        text = response.content.decode()
        assert pro_tier.summary() in text
        assert all(
            "tab" in options["classes"] for _, options in PackageTierAdmin.fieldsets
        )

    def test_the_changelist_shows_the_summary_state_and_filters(
        self, root, pro_tier, default_tier
    ):
        response = root.get(reverse("admin:shop_packagetier_changelist"))

        text = response.content.decode()
        assert pro_tier.summary() in text
        assert default_tier.summary() in text
        for state in ("Default", "Public"):
            assert state in text
        for flag in ("is_active", "is_public", "is_default"):
            assert flag in text

    def test_the_add_form_previews_nothing_until_saved(self, root):
        response = root.get(reverse("admin:shop_packagetier_add"))

        assert response.status_code == 200
        assert "Save the package to see its summary" in response.content.decode()

    def test_a_valid_package_is_created(self, root):
        response = root.post(reverse("admin:shop_packagetier_add"), package_form())

        assert response.status_code == 302
        created = PackageTier.objects.get(slug="silver")
        assert created.summary() == "10 files per month · free · 3 devices · $5/mo"

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"applies_to_free": None}, "at least one item type"),
            ({"limit_files": "0"}, "Set a file limit or a size limit"),
            ({"price": "-1"}, "greater than or equal to 0"),
            ({"device_limit": "0"}, "at least one device"),
            ({"paid_discount_percent": "10"}, "needs the paid item type"),
            (
                {"fair_daily_files": "10", "fair_monthly_files": "5"},
                "monthly file cap cannot be lower",
            ),
        ],
    )
    def test_cross_field_validation_explains_the_problem(
        self, root, overrides, message
    ):
        response = root.post(
            reverse("admin:shop_packagetier_add"), package_form(**overrides)
        )

        assert response.status_code == 200
        assert message in response.content.decode()
        assert not PackageTier.objects.exists()

    def test_only_one_default_package(self, root, default_tier):
        response = root.post(
            reverse("admin:shop_packagetier_add"),
            package_form(
                is_default="on", price="0", duration_days="0", window=WindowKind.DAY
            ),
        )

        assert response.status_code == 200
        assert "already the default" in response.content.decode()

    def test_the_package_page_lists_its_holders(self, root, user, pro_tier):
        services.activate_package(user, pro_tier)

        text = root.get(
            reverse("admin:shop_packagetier_change", args=[pro_tier.pk])
        ).content.decode()

        assert "Holders" in text
        assert user.username in text or user.email in text

    def test_bulk_activate_and_deactivate(self, root, pro_tier, default_tier):
        url = reverse("admin:shop_packagetier_changelist")
        selected = [pro_tier.pk, default_tier.pk]

        root.post(url, {"action": "deactivate_selected", "_selected_action": selected})
        pro_tier.refresh_from_db()
        default_tier.refresh_from_db()
        assert (pro_tier.is_active, default_tier.is_active) == (False, True)

        root.post(url, {"action": "activate_selected", "_selected_action": selected})
        pro_tier.refresh_from_db()
        assert pro_tier.is_active

    def test_duplicate_dialog_creates_a_draft_copy(self, root, pro_tier):
        url = reverse("admin:shop_packagetier_duplicate_package", args=[pro_tier.pk])

        assert root.get(url).status_code == 200
        response = root.post(url, DIALOG)

        copy = PackageTier.objects.get(slug="copy-of-pro")
        assert response.headers["HX-Redirect"].endswith(f"/{copy.pk}/change/")
        assert (copy.is_active, copy.is_public, copy.is_default) == (False,) * 3

    def test_archive_dialog_hides_the_package_and_refuses_the_default(
        self, root, user, pro_tier, default_tier
    ):
        pending = services.create_order(user, pro_tier)

        root.post(
            reverse("admin:shop_packagetier_archive_package", args=[pro_tier.pk]),
            DIALOG,
        )
        root.post(
            reverse("admin:shop_packagetier_archive_package", args=[default_tier.pk]),
            DIALOG,
        )

        pro_tier.refresh_from_db()
        default_tier.refresh_from_db()
        pending.refresh_from_db()
        assert (pro_tier.is_active, pro_tier.is_public) == (False, False)
        assert pending.status == Order.Status.CANCELLED
        assert default_tier.is_active and default_tier.is_public

    def test_dialog_actions_need_permission(self, user, make_user, pro_tier):
        staff_member = make_user("plain-staff", verified=True)
        staff_member.is_staff = True
        staff_member.save()
        staff_member.user_permissions.add(
            Permission.objects.get(codename="view_packagetier")
        )
        client = Client()
        client.force_login(staff_member)

        response = client.post(
            reverse("admin:shop_packagetier_archive_package", args=[pro_tier.pk]),
            DIALOG,
        )

        assert response.status_code == 403
        pro_tier.refresh_from_db()
        assert pro_tier.is_active


class TestUserPackageAdmin:
    def test_the_page_carries_the_quota_override_inline(self, root, user, pro_tier):
        held = services.activate_package(user, pro_tier)

        text = root.get(
            reverse("admin:shop_userpackage_change", args=[held.pk])
        ).content.decode()

        assert "quota_overrides-TOTAL_FORMS" in text
        assert "Quota overrides" in text

    def test_saving_an_override_records_who_granted_it(
        self, root, staff, user, pro_tier
    ):
        held = services.activate_package(user, pro_tier)
        url = reverse("admin:shop_userpackage_change", args=[held.pk])
        data = {
            "user": user.pk,
            "package": pro_tier.pk,
            "status": "active",
            "source": "order",
            "started_at_0": held.started_at.strftime("%Y-%m-%d"),
            "started_at_1": held.started_at.strftime("%H:%M:%S"),
            "payment_reference": "",
            "quota_overrides-TOTAL_FORMS": "1",
            "quota_overrides-INITIAL_FORMS": "0",
            "quota_overrides-MIN_NUM_FORMS": "0",
            "quota_overrides-MAX_NUM_FORMS": "1000",
            "quota_overrides-0-limit_files": "500",
            "quota_overrides-0-reason": "beta",
            "quota_overrides-0-user_package": held.pk,
            "_save": "Save",
        }

        root.post(url, data)

        override = PackageQuotaOverride.objects.get()
        assert (override.limit_files, override.granted_by) == (500, staff)

    def test_cancel_package_dialog_and_bulk_action(self, root, user, pro_tier):
        package = services.activate_package(user, pro_tier)
        url = reverse("admin:shop_userpackage_cancel_package", args=[package.pk])

        root.post(url, DIALOG)
        package.refresh_from_db()
        assert package.status == UserPackage.Status.CANCELLED
        assert package.cancelled_at is not None

        again = services.activate_package(user, pro_tier)
        root.post(
            reverse("admin:shop_userpackage_changelist"),
            {"action": "cancel_selected", "_selected_action": [again.pk]},
        )
        again.refresh_from_db()
        assert again.status == UserPackage.Status.CANCELLED


class TestOrderAdmin:
    def test_the_detail_page_shows_the_items_inline(self, root, order):
        response = root.get(order_url(order, "change"))

        assert response.status_code == 200
        assert "Pro" in response.content.decode()

    def test_the_list_summarises_the_items(self, root, order):
        text = root.get(reverse("admin:shop_order_changelist")).content.decode()

        assert "Pro" in text and "Items" in text

    def test_mark_paid_dialog_provisions_the_order_once(self, root, staff, user, order):
        opened = root.get(order_url(order, "mark_order_paid"))
        first = root.post(
            order_url(order, "mark_order_paid"), {**DIALOG, "reference": "RCPT-42"}
        )
        second = root.post(order_url(order, "mark_order_paid"), DIALOG)

        assert opened.status_code == 200
        assert "HX-Redirect" in first.headers and "HX-Redirect" in second.headers
        order.refresh_from_db()
        assert order.status == Order.Status.PAID
        assert (order.payment_reference, order.processed_by) == ("RCPT-42", staff)
        assert UserPackage.objects.filter(user=user).count() == 1

    def test_cancelled_orders_cannot_be_paid(self, root, order):
        root.post(order_url(order, "cancel_order"), DIALOG)
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED

        root.post(order_url(order, "mark_order_paid"), DIALOG)

        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED
        assert not UserPackage.objects.exists()

    def test_paid_orders_cannot_be_cancelled_but_can_be_refunded(
        self, root, user, order
    ):
        root.post(order_url(order, "mark_order_paid"), DIALOG)
        root.post(order_url(order, "cancel_order"), DIALOG)
        order.refresh_from_db()
        assert order.status == Order.Status.PAID

        root.post(order_url(order, "refund_paid_order"), {**DIALOG, "reason": "dup"})

        order.refresh_from_db()
        assert order.status == Order.Status.REFUNDED
        assert "dup" in order.notes
        assert UserPackage.objects.get(user=user).status == "cancelled"

    def test_bulk_actions_still_work(self, root, order):
        root.post(
            reverse("admin:shop_order_changelist"),
            {"action": "mark_paid", "_selected_action": [order.pk]},
        )

        order.refresh_from_db()
        assert order.status == Order.Status.PAID

    def test_staff_without_change_permission_cannot_run_order_actions(
        self, make_user, order
    ):
        viewer = make_user("shop-viewer")
        viewer.is_staff = True
        viewer.save()
        viewer.user_permissions.add(Permission.objects.get(codename="view_order"))
        client = Client()
        client.force_login(viewer)

        dialog = client.post(order_url(order, "mark_order_paid"), DIALOG)
        bulk = client.post(
            reverse("admin:shop_order_changelist"),
            {"action": "mark_paid", "_selected_action": [order.pk]},
        )

        assert dialog.status_code == 403
        assert bulk.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.PENDING

    def test_orders_cannot_be_added_by_hand(self, root):
        assert root.get(reverse("admin:shop_order_add")).status_code == 403


class TestRegistration:
    def test_the_retired_models_are_gone_and_every_page_renders(
        self, root, staff, order, default_tier
    ):
        labels = {
            model._meta.label
            for model in admin.site._registry
            if model._meta.app_label == "shop"
        }
        assert labels == {
            "shop.PackageTier",
            "shop.UserPackage",
            "shop.PackageQuotaOverride",
            "shop.Order",
            "shop.QuotaConsumption",
            "shop.FeaturedBlogPurchase",
            "shop.UserQuotaOverride",
            "shop.QuotaOverrideAudit",
            "shop.ShopSettings",
        }

        request = RequestFactory().get("/")
        request.user = staff
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label != "shop":
                continue
            name = model._meta.model_name
            assert root.get(reverse(f"admin:shop_{name}_changelist")).status_code == 200
            if model_admin.has_add_permission(request):
                assert root.get(reverse(f"admin:shop_{name}_add")).status_code == 200
        assert root.get(order_url(order, "change")).status_code == 200
