"""Pricing single source of truth: dollars × the wallet rate, everywhere."""

from __future__ import annotations

from decimal import Decimal
from importlib import import_module

import pytest
from django.test import Client
from django.urls import reverse

from apps.shop import wallet_gateway
from apps.shop.models import PackageTier
from apps.wallet import services
from apps.wallet.models import WalletSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def real_wallet(monkeypatch) -> None:
    monkeypatch.setattr(
        wallet_gateway, "_api", lambda: import_module("apps.wallet.api")
    )


@pytest.fixture
def root(staff) -> Client:
    client = Client()
    client.force_login(staff)
    return client


def set_rate(rate: int) -> None:
    cfg = WalletSettings.get_solo()
    cfg.credits_per_currency = rate
    cfg.save()


def tier(price: str = "4.00", **fields) -> PackageTier:
    return PackageTier.objects.create(
        name=fields.pop("name", "Basic"),
        slug=fields.pop("slug", "basic"),
        price=Decimal(price),
        duration_days=30,
        **fields,
    )


class TestDerivation:
    def test_the_credit_price_is_price_times_the_rate(self):
        set_rate(10)

        assert tier("4.00").credit_price == 40

    def test_a_rate_change_reprices_at_once_without_touching_the_package(self):
        package = tier("4.00")
        set_rate(10)
        assert package.credit_price == 40

        set_rate(25)

        assert PackageTier.objects.get(pk=package.pk).credit_price == 100

    def test_an_explicit_override_ignores_the_rate(self):
        package = tier("4.00", credit_price_override=33)
        set_rate(10)
        assert package.credit_price == 33

        set_rate(99)

        assert package.credit_price == 33

    def test_a_package_that_forbids_credits_has_no_credit_price(self):
        assert tier("4.00", allow_credits=False).credit_price == 0

    def test_a_free_package_has_no_credit_price(self):
        assert tier("0").credit_price == 0

    def test_the_label_shows_both_currencies(self):
        set_rate(10)

        assert tier("4.00").price_with_credits == "$4/mo · 40 credits"
        assert wallet_gateway.price_label(Decimal(4)) == "$4.00 · 40 credits"

    def test_without_a_wallet_only_dollars_are_shown(self, monkeypatch):
        monkeypatch.setattr(wallet_gateway, "_api", lambda: None)

        package = tier("4.00")

        assert package.credit_price == 0
        assert package.price_with_credits == "$4/mo"

    def test_a_credit_override_needs_credits_switched_on(self):
        package = PackageTier(
            name="X", slug="x", price=Decimal(4), credit_price_override=9
        )
        package.allow_credits = False

        with pytest.raises(Exception, match="credit_price_override"):
            package.clean()


class TestConversion:
    @pytest.mark.parametrize(
        ("amount", "rate", "expected"),
        [
            ("9", 10, 90),
            ("0.05", 10, 1),
            ("0.15", 10, 2),
            ("0.04", 10, 1),
            ("0", 10, 0),
            ("4.00", 1, 4),
            ("4.00", 250, 1000),
        ],
    )
    def test_to_credits_rounds_half_up_and_never_makes_a_price_free(
        self, amount, rate, expected
    ):
        assert services.to_credits_at(Decimal(amount), rate) == expected

    def test_the_wallet_uses_the_configured_rate(self):
        set_rate(40)

        assert services.to_credits(Decimal("2.5")) == 100


class TestPackageEditor:
    def test_the_form_reads_out_the_credits_equivalent(self, root):
        set_rate(10)
        package = tier("9.00")

        page = root.get(reverse("admin:shop_packagetier_change", args=[package.pk]))
        text = page.content.decode()

        assert page.status_code == 200
        assert 'id="mf-credits-equivalent"' in text
        assert "$9/mo · 90 credits" in text
        assert 'hx-get="' + reverse("admin:shop_packagetier_credits_preview") in text

    def test_the_preview_endpoint_converts_the_typed_price(self, root):
        set_rate(10)
        url = reverse("admin:shop_packagetier_credits_preview")

        assert root.get(url, {"price": "4"}).content.decode() == "$4.00 · 40 credits"
        assert root.get(url, {"price": "0"}).content.decode() == "Free"
        assert "valid" in root.get(url, {"price": "abc"}).content.decode()
        assert "valid" in root.get(url, {"price": "-3"}).content.decode()
        assert "valid" in root.get(url, {"price": "NaN"}).content.decode()
        assert "valid" in root.get(url, {"price": "1e99"}).content.decode()

    def test_the_preview_needs_the_view_permission(self, make_user):
        clerk = make_user("clerk")
        clerk.is_staff = True
        clerk.save()
        client = Client()
        client.force_login(clerk)

        response = client.get(
            reverse("admin:shop_packagetier_credits_preview"), {"price": "4"}
        )

        assert response.status_code == 403

    def test_the_editor_groups_every_field_the_owner_listed(self, root):
        package = tier("9.00")
        text = root.get(
            reverse("admin:shop_packagetier_change", args=[package.pk])
        ).content.decode()

        for name in (
            "name",
            "slug",
            "duration_days",
            "price",
            "limit_files",
            "limit_size",
            "window",
            "applies_to_free",
            "applies_to_featured",
            "applies_to_paid",
            "is_active",
            "slot_reset_credits",
        ):
            assert f'name="{name}"' in text, name


class TestWalletSettingsPage:
    def test_the_page_is_called_credits_and_pricing_and_warns_about_the_ledger(
        self, root
    ):
        WalletSettings.get_solo()

        text = root.get(
            reverse("admin:wallet_walletsettings_changelist"), follow=True
        ).content.decode()

        assert "Credits &amp; pricing" in text
        assert "the ledger is never rewritten" in text
        assert "$9.00 = <strong>90 credits</strong>" in text

    def test_changing_the_rate_warns_and_reprices_without_rewriting_the_ledger(
        self, root, user
    ):
        set_rate(10)
        services.credit(user, 100, reason="topup", key="rate:fund")
        package = tier("4.00")
        cfg = WalletSettings.get_solo()
        url = reverse("admin:wallet_walletsettings_change")

        fields = {
            "currency_label": "USD",
            "credits_per_currency": "20",
            "topups_enabled": "on",
            "payouts_enabled": "on",
            "min_topup_credits": cfg.min_topup_credits,
            "max_topup_credits": cfg.max_topup_credits,
            "max_open_topups": cfg.max_open_topups,
            "min_payout_credits": cfg.min_payout_credits,
            "payout_fee_percent": "0.00",
        }
        response = root.post(url, fields, follow=True)

        assert response.status_code == 200
        assert "Prices were re-derived; the ledger was not rewritten." in (
            response.content.decode()
        )
        assert PackageTier.objects.get(pk=package.pk).credit_price == 80
        assert services.balance(user) == 100
