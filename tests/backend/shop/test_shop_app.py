"""Shop services and public views around the single Package concept."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.shop import services
from apps.shop import urls as shop_urls
from apps.shop.models import Order, PackageTier, UserPackage
from apps.shop.tasks import expire_packages

pytestmark = pytest.mark.django_db


@pytest.fixture
def pro(pro_tier) -> PackageTier:
    return pro_tier


@pytest.fixture
def gold(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("20.00"),
        credit_price_override=500,
        duration_days=90,
        limit_files=100,
        applies_to_featured=True,
    )


class TestOnlinePaymentsDisabled:
    def test_the_flag_is_off_and_visible_in_the_ui(self, client, pro):
        assert services.ONLINE_PAYMENTS_ENABLED is False

        for name, kwargs in (
            ("shop:pricing", {}),
            ("shop:package_detail", {"slug": "pro"}),
        ):
            response = client.get(reverse(name, kwargs=kwargs))
            assert "Online payments are not available" in response.content.decode()

    def test_no_payment_provider_routes_remain(self):
        names = {route.name for route in shop_urls.urlpatterns if route.name}

        assert not {name for name in names if "stripe" in name or "webhook" in name}


class TestOrderFlow:
    def test_create_order_is_pending_and_priced_from_the_package(self, user, pro):
        order = services.create_order(user, pro)

        assert (order.status, order.total) == (Order.Status.PENDING, Decimal("9.00"))
        item = order.items.get()
        assert (item.package, item.duration_days, item.description) == (pro, 30, "Pro")
        assert not UserPackage.objects.filter(user=user).exists()

    def test_repeated_requests_reuse_the_pending_order(self, user, pro, gold):
        first = services.create_order(user, pro)
        again = services.create_order(user, pro)
        other = services.create_order(user, gold)

        assert first.pk == again.pk
        assert other.pk != first.pk

    def test_unavailable_free_and_default_packages_are_rejected(
        self, user, pro, default_tier
    ):
        PackageTier.objects.filter(pk=pro.pk).update(is_public=False)
        pro.refresh_from_db()
        with pytest.raises(services.OrderError, match="not available"):
            services.create_order(user, pro)
        with pytest.raises(services.OrderError, match="not available"):
            services.create_order(user, default_tier)
        free_paid = PackageTier.objects.create(
            name="Zero", slug="zero", price=Decimal(0), limit_files=1
        )
        with pytest.raises(services.OrderError, match="free"):
            services.create_order(user, free_paid)

    def test_mark_paid_provisions_once_and_is_idempotent(self, user, staff, pro):
        order = services.create_order(user, pro)

        services.mark_order_paid(order.pk, staff_user=staff, reference="BANK-1")
        services.mark_order_paid(order.pk, staff_user=staff, reference="BANK-2")

        order.refresh_from_db()
        assert (order.status, order.payment_reference) == (Order.Status.PAID, "BANK-1")
        assert order.processed_by == staff
        held = UserPackage.objects.get(user=user)
        assert (held.package, held.status, held.source) == (
            pro,
            UserPackage.Status.ACTIVE,
            UserPackage.Source.ORDER,
        )
        assert held.order == order
        assert held.expires_at is not None
        assert held.expires_at - held.started_at == timedelta(days=30)

    def test_the_order_snapshot_decides_the_duration(self, user, staff, pro):
        order = services.create_order(user, pro)
        PackageTier.objects.filter(pk=pro.pk).update(duration_days=0)

        services.mark_order_paid(order.pk, staff_user=staff)

        assert UserPackage.objects.get(user=user).expires_at is not None

    def test_lifetime_packages_never_expire(self, user, staff):
        forever = PackageTier.objects.create(
            name="Forever", slug="forever", price=Decimal(99), duration_days=0
        )
        order = services.create_order(user, forever)

        services.mark_order_paid(order.pk, staff_user=staff)

        assert UserPackage.objects.get(user=user).expires_at is None

    def test_cancelled_orders_cannot_be_paid_and_paid_orders_cannot_be_cancelled(
        self, user, staff, pro, gold
    ):
        cancelled = services.create_order(user, pro)
        services.cancel_order(cancelled.pk, staff_user=staff)
        with pytest.raises(services.OrderError):
            services.mark_order_paid(cancelled.pk, staff_user=staff)

        paid = services.create_order(user, gold)
        services.mark_order_paid(paid.pk, staff_user=staff)
        with pytest.raises(services.OrderError):
            services.cancel_order(paid.pk, staff_user=staff)

    def test_activating_a_package_expires_the_previous_one(
        self, user, pro, gold, default_tier
    ):
        first = services.activate_package(user, pro)
        services.activate_package(user, gold)

        first.refresh_from_db()
        assert first.status == UserPackage.Status.EXPIRED
        assert UserPackage.objects.filter(user=user, status="active").count() == 1

    def test_renewing_the_same_package_stacks_the_period(self, user, staff, pro):
        first = services.activate_package(user, pro)

        renewed = services.activate_package(user, pro)

        assert renewed.expires_at is not None and first.expires_at is not None
        assert renewed.expires_at == first.expires_at + timedelta(days=30)

    def test_database_allows_only_one_live_package_per_user(self, user, pro, gold):
        UserPackage.objects.create(user=user, package=pro)
        with pytest.raises(IntegrityError), transaction.atomic():
            UserPackage.objects.create(user=user, package=gold)

    def test_expiry_task_expires_overdue_packages(self, user, staff, pro):
        past = timezone.now() - timedelta(days=1)
        overdue = UserPackage.objects.create(user=user, package=pro, expires_at=past)
        UserPackage.objects.create(user=staff, package=pro)

        result = expire_packages()

        overdue.refresh_from_db()
        assert result == {"packages": 1}
        assert overdue.status == UserPackage.Status.EXPIRED
        assert expire_packages() == {"packages": 0}


class TestPackageAdministrationServices:
    def test_deactivating_keeps_holders_and_blocks_orders(self, user, pro):
        services.activate_package(user, pro)

        services.set_package_active(pro, active=False)

        assert UserPackage.objects.get(user=user).status == "active"
        with pytest.raises(services.OrderError):
            services.create_order(user, pro)
        services.set_package_active(pro, active=True)
        assert services.create_order(user, pro)

    def test_the_default_package_cannot_be_deactivated_or_archived(self, default_tier):
        with pytest.raises(services.OrderError):
            services.set_package_active(default_tier, active=False)
        with pytest.raises(services.OrderError):
            services.archive_package(default_tier)

    def test_archiving_hides_the_package_and_cancels_pending_orders(
        self, user, other, pro
    ):
        pending = services.create_order(user, pro)

        services.archive_package(pro)

        pro.refresh_from_db()
        pending.refresh_from_db()
        assert (pro.is_active, pro.is_public) == (False, False)
        assert pending.status == Order.Status.CANCELLED

    def test_duplicate_makes_a_hidden_inactive_non_default_copy(self, default_tier):
        copy = services.duplicate_package(default_tier)
        second = services.duplicate_package(default_tier)

        assert copy.name == "Copy of Free" and second.name == "Copy of Free 2"
        assert copy.slug == "copy-of-free" and second.slug == "copy-of-free-2"
        assert (copy.is_default, copy.is_active, copy.is_public) == (False,) * 3
        assert copy.limit_files == default_tier.limit_files
        assert copy.summary() == default_tier.summary()


class TestPublicViews:
    def test_public_pages_render(self, client, pro, default_tier):
        for name, kwargs in (
            ("shop:pricing", {}),
            ("shop:package_detail", {"slug": "pro"}),
        ):
            assert client.get(reverse(name, kwargs=kwargs)).status_code == 200

    def test_the_shop_root_redirects_to_the_pricing_page(self, client):
        response = client.get("/shop/")

        assert response.status_code == 302
        assert response["Location"] == "/shop/pricing/"
        assert reverse("shop:pricing") == "/shop/pricing/"

    def test_inactive_and_hidden_packages_are_not_public(self, client, pro):
        PackageTier.objects.filter(pk=pro.pk).update(is_active=False)

        assert (
            client.get(
                reverse("shop:package_detail", kwargs={"slug": "pro"})
            ).status_code
            == 404
        )
        assert "Pro" not in client.get(reverse("shop:pricing")).content.decode()

    def test_account_pages_require_login(self, client):
        for name in ("shop:my_package", "shop:order_history"):
            assert client.get(reverse(name)).status_code == 302

    def test_placing_an_order_requires_post_login_verified_email_and_csrf(
        self, client, make_user, pro
    ):
        url = reverse("shop:place_order", kwargs={"slug": "pro"})
        assert client.get(url).status_code == 405
        assert client.post(url).status_code == 302
        assert Order.objects.count() == 0

        client.force_login(make_user("unverified", verified=False))
        client.post(url)
        assert Order.objects.count() == 0

        member = make_user("member")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(member)
        assert csrf_client.post(url).status_code == 403

        client.force_login(member)
        response = client.post(url)
        order = Order.objects.get()
        assert response.status_code == 302
        assert response["Location"] == reverse("shop:order_detail", args=[order.pk])
        assert (order.user, order.status) == (member, Order.Status.PENDING)

    def test_default_and_unknown_packages_cannot_be_ordered(
        self, client, user, default_tier
    ):
        client.force_login(user)

        for slug in (default_tier.slug, "nope"):
            response = client.post(reverse("shop:place_order", kwargs={"slug": slug}))
            assert response.status_code == 404

    def test_order_history_only_shows_own_orders(self, client, user, other, pro, gold):
        mine = services.create_order(user, pro)
        services.create_order(other, gold)
        client.force_login(user)

        response = client.get(reverse("shop:order_history"))

        assert f"#{mine.pk}" in response.content.decode()
        assert len(response.context["orders"]) == 1

    def test_my_package_shows_the_package_summary_and_history(
        self, client, user, pro, default_tier
    ):
        services.activate_package(user, pro)
        client.force_login(user)

        response = client.get(reverse("shop:my_package"))

        text = response.content.decode()
        assert "Pro" in text and pro.summary() in text
        assert response.context["package"].package == pro
