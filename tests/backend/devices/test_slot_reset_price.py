"""The slot-reset price: one admin-managed global price, optional per-package override."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.devices import credits as wallet_credits
from apps.devices import slots
from apps.devices.models import DeviceSlotReset
from apps.shop.models import PackageTier, UserPackage
from apps.wallet import api as wallet
from apps.wallet import registry
from apps.wallet.models import CreditPrice, LedgerEntry
from apps.wallet.services import sync_registry

pytestmark = pytest.mark.django_db

SINK = "device.slot_reset"


@pytest.fixture(autouse=True)
def economy() -> None:
    sync_registry()


@pytest.fixture(name="funded")
def funded_fixture(user):
    wallet.credit(user, 500, reason=wallet.LedgerReason.ADJUSTMENT, key="seed-price")
    return user


@pytest.fixture(name="full_account")
def full_account_fixture(user, approve_browsers):
    return approve_browsers(user, 3)


@pytest.fixture
def seeded() -> None:
    call_command("seed_packages")


def hold(user, slug: str) -> None:
    UserPackage.objects.create(
        user=user, package=PackageTier.objects.get(slug=slug), source="admin"
    )


def test_the_registry_default_is_ten_credits():
    assert wallet_credits.SLOT_RESET_DEFAULT_CREDITS == 10
    assert CreditPrice.objects.get(sink_key=SINK).credits == 10


def test_sync_never_overwrites_a_row_staff_already_priced():
    CreditPrice.objects.filter(sink_key=SINK).update(credits=50)

    sync_registry()

    assert CreditPrice.objects.get(sink_key=SINK).credits == 50


class TestGlobalPrice:
    @pytest.mark.usefixtures("full_account")
    def test_an_admin_edit_takes_effect_everywhere_at_once(self, client, staff, funded):
        admin = Client()
        admin.force_login(staff)
        row = CreditPrice.objects.get(sink_key=SINK)
        client.force_login(funded)
        assert (
            "Pay 10 credits and reset"
            in client.get(reverse("devices:slot_pay_credits")).content.decode()
        )

        response = admin.post(
            reverse("admin:wallet_creditprice_change", args=[row.pk]),
            {"credits": "25", "enabled": "on"},
        )

        assert response.status_code == 302
        assert wallet_credits.price(funded) == 25
        assert slots.slot_options(funded).credits_price == 25
        assert (
            "Pay 25 credits and reset"
            in client.get(reverse("devices:slot_pay_credits")).content.decode()
        )
        assert (
            "Pay 25 credits"
            in client.get(reverse("devices:my_devices")).content.decode()
        )
        assert "25 credits" in client.get(reverse("wallet:spend")).content.decode()
        reset = slots.reset_slots_with_credits(funded, key="p" * 32)
        assert reset.credits_spent == 25
        assert LedgerEntry.objects.get(key=reset.ledger_key).amount == 25

    def test_a_disabled_sink_means_credits_are_unavailable(self, user):
        CreditPrice.objects.filter(sink_key=SINK).update(enabled=False)

        assert wallet_credits.price(user) is None


class TestPackageOverride:
    @pytest.mark.usefixtures("seeded")
    def test_the_free_seed_pays_twenty_and_the_others_the_global_price(
        self, user, make_user
    ):
        assert PackageTier.objects.get(slug="free").slot_reset_credits == 20
        assert PackageTier.objects.get(slug="premium").slot_reset_credits is None
        assert PackageTier.objects.get(slug="gold").slot_reset_credits is None
        premium = make_user("premium_holder")
        hold(premium, "premium")

        assert wallet_credits.price(user) == 20
        assert wallet_credits.price(premium) == 10

    @pytest.mark.usefixtures("seeded", "full_account")
    def test_a_package_override_is_what_the_dialog_shows_and_the_ledger_debits(
        self, client, funded
    ):
        client.force_login(funded)

        body = client.get(reverse("devices:slot_pay_credits")).content.decode()
        reset = slots.reset_slots_with_credits(funded, key="q" * 32)

        assert "Pay 20 credits and reset" in body
        assert reset.credits_spent == 20
        assert wallet.balance(funded) == 480
        assert DeviceSlotReset.objects.filter(user=funded).count() == 1

    @pytest.mark.usefixtures("seeded")
    def test_a_null_override_falls_back_to_the_global_price(self, make_user):
        premium = make_user("premium_two")
        hold(premium, "premium")
        CreditPrice.objects.filter(sink_key=SINK).update(credits=15)

        assert wallet_credits.price(premium) == 15

    @pytest.mark.usefixtures("seeded")
    def test_changing_the_override_applies_to_the_next_read(self, user):
        PackageTier.objects.filter(slug="free").update(slot_reset_credits=7)

        assert wallet_credits.price(user) == 7

    @pytest.mark.usefixtures("seeded")
    def test_price_modifiers_still_apply_to_an_override(self, user, monkeypatch):
        def half(sink: str, total: int, _quantity: int, _context) -> int:
            return total // 2 if sink == SINK else total

        monkeypatch.setattr(registry, "_price_modifiers", [half])

        assert wallet_credits.price(user) == 10

    @pytest.mark.usefixtures("seeded")
    def test_the_spend_page_shows_each_account_its_own_price(
        self, client, user, make_user
    ):
        premium = make_user("premium_three")
        hold(premium, "premium")

        client.force_login(user)
        free_page = client.get(reverse("wallet:spend")).content.decode()
        client.force_login(premium)
        premium_page = client.get(reverse("wallet:spend")).content.decode()

        assert "20 credits" in free_page
        assert "10 credits" in premium_page

    def test_the_model_rejects_a_zero_override(self):
        tier = PackageTier(name="Odd", slug="odd", slot_reset_credits=0)

        with pytest.raises(Exception, match="slot_reset_credits"):
            tier.full_clean()

    def test_the_package_admin_explains_the_override(self, staff):
        client = Client()
        client.force_login(staff)
        free = PackageTier.objects.create(
            name="Free", slug="free", is_default=True, duration_days=0
        )

        page = client.get(reverse("admin:shop_packagetier_change", args=[free.pk]))

        assert page.status_code == 200
        assert b"Device slot reset price" in page.content
        assert b"Leave empty to use the global price" in page.content
