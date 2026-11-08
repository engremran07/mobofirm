"""The shop side of featured blog articles: package access, credit purchase, contract."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from importlib import import_module

import pytest
from django.contrib import admin as django_admin
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.app_service import AppService
from apps.shop import api, wallet_gateway
from apps.shop.models import (
    FeaturedBlogPurchase,
    FeaturedBlogUnlock,
    PackageTier,
    UserPackage,
)
from apps.wallet import api as wallet
from apps.wallet.models import CreditPrice, LedgerEntry, WalletSettings
from apps.wallet.services import sync_registry

pytestmark = pytest.mark.django_db

POST = 41
PRICE = Decimal("2.50")


@pytest.fixture(autouse=True)
def real_wallet(monkeypatch) -> None:
    monkeypatch.setattr(
        wallet_gateway, "_api", lambda: import_module("apps.wallet.api")
    )
    sync_registry()
    cfg = WalletSettings.get_solo()
    cfg.credits_per_currency = 10
    cfg.save()


@pytest.fixture
def funded(user):
    wallet.credit(user, 100, reason=wallet.LedgerReason.ADJUSTMENT, key="seed-blog")
    return user


def package(limit: int = 0, **fields) -> PackageTier:
    return PackageTier.objects.create(
        name="Reader",
        slug="reader",
        price=Decimal("5.00"),
        duration_days=30,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured_blog=True,
        featured_blog_limit=limit,
        **fields,
    )


def hold(user, tier: PackageTier) -> None:
    UserPackage.objects.create(user=user, package=tier, source="admin")


class TestContract:
    def test_the_blog_resolves_the_contract_through_app_service(self):
        module = AppService.get("shop")

        assert module is api
        for name in (
            "featured_blog_access",
            "purchase_featured_blog",
            "featured_blog_buyers",
            "purchased_featured_blog_ids",
        ):
            assert callable(getattr(module, name))
        access = api.BlogAccess(True, "purchase", None, "")
        assert (access.allowed, access.via, access.credit_price, access.reason) == (
            True,
            "purchase",
            None,
            "",
        )

    def test_the_sink_is_registered_and_derived(self):
        row = CreditPrice.objects.get(sink_key="blog.featured_purchase")

        assert row.app == "blog"


class TestAccess:
    def test_anonymous_readers_are_denied_with_the_credit_price(self):
        from django.contrib.auth.models import AnonymousUser

        access = api.featured_blog_access(AnonymousUser(), POST, PRICE)

        assert (access.allowed, access.via, access.credit_price) == (
            False,
            "anonymous",
            25,
        )

    def test_a_reader_without_a_package_is_denied_and_quoted_the_derived_price(
        self, user
    ):
        access = api.featured_blog_access(user, POST, PRICE)

        assert not access.allowed
        assert access.credit_price == 25
        assert access.reason

    def test_the_price_follows_the_wallet_rate(self, user):
        cfg = WalletSettings.get_solo()
        cfg.credits_per_currency = 40
        cfg.save()

        assert api.featured_blog_access(user, POST, PRICE).credit_price == 100

    def test_a_post_without_a_price_has_no_credit_price(self, user):
        assert api.featured_blog_access(user, POST, None).credit_price is None
        assert api.featured_blog_access(user, POST, Decimal(0)).credit_price is None

    def test_a_package_with_featured_blogs_unlocks_and_records_it_once(self, user):
        hold(user, package(limit=2))

        first = api.featured_blog_access(user, POST, PRICE)
        again = api.featured_blog_access(user, POST, PRICE)

        assert (first.allowed, first.via) == (True, "package")
        assert again.allowed
        assert FeaturedBlogUnlock.objects.filter(user=user).count() == 1

    def test_the_package_quota_is_counted_per_article(self, user):
        hold(user, package(limit=2))

        assert api.featured_blog_access(user, 1, PRICE).allowed
        assert api.featured_blog_access(user, 2, PRICE).allowed
        third = api.featured_blog_access(user, 3, PRICE)

        assert (third.allowed, third.via) == (False, "quota")
        assert third.credit_price == 25
        assert "used up" in third.reason
        assert api.featured_blog_access(user, 1, PRICE).allowed

    def test_the_quota_window_rolls_over(self, user):
        hold(user, package(limit=1, window="day"))
        FeaturedBlogUnlock.objects.create(
            user=user, post_id=1, unlocked_at=timezone.now() - timedelta(days=3)
        )

        assert api.featured_blog_access(user, 2, PRICE).allowed

    def test_zero_limit_means_unlimited(self, user):
        hold(user, package(limit=0))

        assert all(api.featured_blog_access(user, n, PRICE).allowed for n in range(6))

    def test_a_package_without_featured_blogs_does_not_unlock(self, user):
        tier = package()
        tier.applies_to_featured_blog = False
        tier.save()
        hold(user, tier)

        assert not api.featured_blog_access(user, POST, PRICE).allowed
        assert not FeaturedBlogUnlock.objects.exists()


class TestPurchase:
    def test_buying_spends_the_derived_credits_and_grants_lifetime_access(self, funded):
        result = api.purchase_featured_blog(funded, POST, PRICE, "k1")

        assert (result.allowed, result.via) == (True, "purchase")
        assert wallet.balance(funded) == 75
        purchase = FeaturedBlogPurchase.objects.get()
        assert (purchase.user, purchase.post_id, purchase.credits) == (funded, POST, 25)
        entry = LedgerEntry.objects.get(event_key="blog.featured_purchase")
        assert entry.amount == 25
        assert api.featured_blog_access(funded, POST, PRICE).via == "purchase"
        assert api.purchased_featured_blog_ids(funded) == [POST]

    def test_the_same_key_is_idempotent(self, funded):
        api.purchase_featured_blog(funded, POST, PRICE, "k1")
        again = api.purchase_featured_blog(funded, POST, PRICE, "k1")

        assert again.allowed
        assert wallet.balance(funded) == 75
        assert FeaturedBlogPurchase.objects.count() == 1

    def test_another_key_for_an_owned_post_never_charges_twice(self, funded):
        api.purchase_featured_blog(funded, POST, PRICE, "k1")
        again = api.purchase_featured_blog(funded, POST, PRICE, "k2")

        assert (again.allowed, again.via) == (True, "purchase")
        assert wallet.balance(funded) == 75

    def test_a_key_cannot_be_replayed_by_another_account(self, funded, other):
        api.purchase_featured_blog(funded, POST, PRICE, "k1")
        wallet.credit(other, 100, reason=wallet.LedgerReason.ADJUSTMENT, key="s2")

        result = api.purchase_featured_blog(other, POST, PRICE, "k1")

        assert (result.allowed, result.via) == (False, "conflict")
        assert wallet.balance(other) == 100

    def test_not_enough_credits_charges_and_records_nothing(self, user):
        wallet.credit(user, 10, reason=wallet.LedgerReason.ADJUSTMENT, key="tiny")

        result = api.purchase_featured_blog(user, POST, PRICE, "k1")

        assert (result.allowed, result.via, result.credit_price) == (
            False,
            "insufficient",
            25,
        )
        assert "15 more credits" in result.reason
        assert wallet.balance(user) == 10
        assert not FeaturedBlogPurchase.objects.exists()

    def test_a_package_holder_is_never_charged(self, funded):
        hold(funded, package(limit=3))

        result = api.purchase_featured_blog(funded, POST, PRICE, "k1")

        assert (result.allowed, result.via) == (True, "package")
        assert wallet.balance(funded) == 100
        assert not FeaturedBlogPurchase.objects.exists()
        assert FeaturedBlogUnlock.objects.filter(user=funded).count() == 1

    def test_a_holder_whose_quota_is_used_up_may_buy(self, funded):
        hold(funded, package(limit=1))
        api.featured_blog_access(funded, 1, PRICE)

        result = api.purchase_featured_blog(funded, POST, PRICE, "k1")

        assert (result.allowed, result.via) == (True, "purchase")
        assert wallet.balance(funded) == 75

    def test_a_post_without_a_price_cannot_be_bought(self, funded):
        result = api.purchase_featured_blog(funded, POST, None, "k1")

        assert (result.allowed, result.via) == (False, "unavailable")
        assert wallet.balance(funded) == 100

    def test_anonymous_visitors_cannot_buy(self):
        from django.contrib.auth.models import AnonymousUser

        result = api.purchase_featured_blog(AnonymousUser(), POST, PRICE, "k1")

        assert not result.allowed

    def test_a_disabled_sink_means_credits_are_unavailable(self, funded):
        CreditPrice.objects.filter(sink_key="blog.featured_purchase").update(
            enabled=False
        )

        result = api.purchase_featured_blog(funded, POST, PRICE, "k1")

        assert (result.allowed, result.via) == (False, "unavailable")
        assert wallet.balance(funded) == 100

    def test_a_failure_after_the_spend_rolls_the_spend_back(self, funded, monkeypatch):
        def explode(**kwargs):
            raise RuntimeError("database went away")

        monkeypatch.setattr(FeaturedBlogPurchase.objects, "create", explode)

        with pytest.raises(RuntimeError):
            api.purchase_featured_blog(funded, POST, PRICE, "k1")

        assert wallet.balance(funded) == 100
        assert not LedgerEntry.objects.filter(
            event_key="blog.featured_purchase"
        ).exists()

    def test_the_unique_constraints_hold_at_the_database(self, funded):
        FeaturedBlogPurchase.objects.create(
            user=funded, post_id=POST, credits=5, key="a"
        )

        with pytest.raises(Exception, match="UNIQUE|unique"):
            FeaturedBlogPurchase.objects.create(
                user=funded, post_id=POST, credits=5, key="b"
            )

    def test_buyers_are_listed_for_the_blog_admin(self, funded):
        api.purchase_featured_blog(funded, POST, PRICE, "k1")

        rows = api.featured_blog_buyers(POST)

        assert [(row["user_id"], row["credits"]) for row in rows] == [(funded.pk, 25)]
        assert api.featured_blog_buyers(POST + 1) == []


class TestPackageModelAndAdmin:
    def test_a_limit_needs_the_item_type(self):
        tier = PackageTier(
            name="Odd",
            slug="odd",
            featured_blog_limit=3,
            applies_to_featured_blog=False,
        )

        with pytest.raises(ValidationError, match="Featured blog articles"):
            tier.clean()

    def test_the_summary_mentions_featured_blogs(self):
        tier = package(limit=5)

        assert "5 articles of featured blogs" in tier.summary()
        assert "featured blogs" in package_unlimited_summary()

    def test_the_package_admin_has_the_fields_and_purchases_are_read_only(self, staff):
        from django.test import Client
        from django.urls import reverse

        client = Client()
        client.force_login(staff)
        tier = package(limit=5)

        page = client.get(reverse("admin:shop_packagetier_change", args=[tier.pk]))
        listing = client.get(reverse("admin:shop_featuredblogpurchase_changelist"))
        add = client.get(reverse("admin:shop_featuredblogpurchase_add"))

        assert page.status_code == 200
        assert b"applies_to_featured_blog" in page.content
        assert b"featured_blog_limit" in page.content
        assert listing.status_code == 200
        assert add.status_code == 403
        assert django_admin.site.is_registered(FeaturedBlogPurchase)


def package_unlimited_summary() -> str:
    return PackageTier(
        name="Unlimited readers", slug="ur", applies_to_featured_blog=True
    ).summary()
