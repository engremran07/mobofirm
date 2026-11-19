"""Wallet admin: every page loads and staff actions go through the services."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.wallet import admin as wallet_admin
from apps.wallet import reconcile, services
from apps.wallet.models import (
    LedgerEntry,
    LedgerReason,
    PayoutRequest,
    TopUpRequest,
    Wallet,
    WalletRepair,
    WalletSettings,
    ledger_posting,
)

pytestmark = pytest.mark.django_db

DIALOG = {"_form_submitted": "on"}
MODELS = (
    "walletsettings",
    "wallet",
    "ledgerentry",
    "rewardrule",
    "creditprice",
    "rewardrefusal",
    "escrowhold",
    "walletrepair",
    "topuprequest",
    "payoutrequest",
)


def test_every_wallet_model_is_registered_and_its_changelist_loads(
    admin_client: Client,
) -> None:
    services.sync_registry()
    registered = {
        model._meta.model_name
        for model in apps.get_app_config("wallet").get_models()
        if admin.site.is_registered(model)
    }
    assert registered == set(MODELS)

    for name in MODELS:
        if name == "walletsettings":
            url = reverse("admin:wallet_walletsettings_changelist")
        else:
            url = reverse(f"admin:wallet_{name}_changelist")
        assert admin_client.get(url).status_code == 200, name


def test_the_ledger_is_read_only_in_the_admin(admin_client: Client, user: Any) -> None:
    entry = services.credit(user, 5, reason=LedgerReason.TOPUP, key="admin:ledger")

    detail = admin_client.get(
        reverse("admin:wallet_ledgerentry_change", args=[entry.pk])
    )
    add = admin_client.get(reverse("admin:wallet_ledgerentry_add"))
    delete = admin_client.post(
        reverse("admin:wallet_ledgerentry_delete", args=[entry.pk]), {"post": "yes"}
    )

    assert detail.status_code == 200
    assert add.status_code == 403
    assert delete.status_code == 403
    assert LedgerEntry.objects.filter(pk=entry.pk).exists()


def test_wallet_detail_shows_ledger_tabs_and_the_integrity_check(
    admin_client: Client, user: Any
) -> None:
    services.credit(user, 5, reason=LedgerReason.TOPUP, key="admin:wallet")
    wallet = Wallet.objects.get(user=user)

    page = admin_client.get(reverse("admin:wallet_wallet_change", args=[wallet.pk]))

    assert page.status_code == 200
    assert b"Consistent (1 entries)" in page.content


def test_staff_adjustment_dialog_changes_the_balance_with_a_reason(
    admin_client: Client, user: Any
) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="admin:fund")
    wallet = Wallet.objects.get(user=user)
    url = reverse("admin:wallet_wallet_adjust_balance", args=[wallet.pk])

    assert admin_client.get(url).status_code == 200
    rejected = admin_client.post(url, {**DIALOG, "credits": "0", "reason": "no"})
    done = admin_client.post(
        url, {**DIALOG, "credits": "-20", "reason": "Fraud clawback"}
    )

    assert rejected.status_code == 200
    assert done.status_code in (200, 302)
    assert services.balance(user) == 30
    entry = LedgerEntry.objects.get(reason=LedgerReason.ADJUSTMENT)
    assert entry.note == "Fraud clawback"


def test_adjustment_without_the_permission_is_denied(user: Any, make_user: Any) -> None:
    clerk = make_user("clerk")
    clerk.is_staff = True
    clerk.save()
    clerk.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="wallet",
            codename__in=["view_wallet", "change_wallet"],
        )
    )
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="admin:fund2")
    wallet = Wallet.objects.get(user=user)
    client = Client()
    client.force_login(clerk)

    response = client.post(
        reverse("admin:wallet_wallet_adjust_balance", args=[wallet.pk]),
        {**DIALOG, "credits": "-20", "reason": "sneaky"},
    )

    assert response.status_code == 403
    assert services.balance(user) == 50


def test_freeze_and_unfreeze_dialogs(admin_client: Client, user: Any) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="admin:fund3")
    wallet = Wallet.objects.get(user=user)

    admin_client.post(
        reverse("admin:wallet_wallet_freeze_wallet", args=[wallet.pk]),
        {**DIALOG, "reason": "Chargeback"},
    )
    wallet.refresh_from_db()
    assert (wallet.is_frozen, wallet.frozen_reason) == (True, "Chargeback")

    admin_client.post(
        reverse("admin:wallet_wallet_unfreeze_wallet", args=[wallet.pk]), DIALOG
    )
    wallet.refresh_from_db()
    assert wallet.is_frozen is False


def test_topup_confirmation_dialog_grants_the_credits_once(
    admin_client: Client, user: Any
) -> None:
    request = services.request_topup(user, 100)
    url = reverse("admin:wallet_topuprequest_confirm_topup", args=[request.pk])

    admin_client.post(url, {**DIALOG, "reference": "Receipt 5"})
    admin_client.post(url, {**DIALOG, "reference": "Receipt 5"})

    request.refresh_from_db()
    assert request.status == TopUpRequest.Status.PAID
    assert request.payment_reference == "Receipt 5"
    assert services.balance(user) == 100


def test_payout_dialogs_walk_the_request_through_its_states(
    admin_client: Client, user: Any
) -> None:
    services.credit(user, 600, reason=LedgerReason.EARNING, key="admin:sale")
    payout = services.request_payout(user, 600, method="bank", destination="DE00")

    admin_client.post(
        reverse("admin:wallet_payoutrequest_approve_request", args=[payout.pk]), DIALOG
    )
    admin_client.post(
        reverse("admin:wallet_payoutrequest_mark_request_paid", args=[payout.pk]),
        {**DIALOG, "reference": "Wire 77"},
    )

    payout.refresh_from_db()
    assert (payout.status, payout.payment_reference) == (
        PayoutRequest.Status.PAID,
        "Wire 77",
    )


def test_payout_rejection_dialog_requires_a_reason_and_refunds(
    admin_client: Client, user: Any
) -> None:
    services.credit(user, 600, reason=LedgerReason.EARNING, key="admin:sale2")
    payout = services.request_payout(user, 600, method="bank", destination="DE00")
    url = reverse("admin:wallet_payoutrequest_reject_request", args=[payout.pk])

    admin_client.post(url, {**DIALOG, "reason": ""})
    payout.refresh_from_db()
    assert payout.status == PayoutRequest.Status.PENDING

    admin_client.post(url, {**DIALOG, "reason": "Account mismatch"})
    payout.refresh_from_db()
    assert payout.status == PayoutRequest.Status.REJECTED
    assert services.balance(user) == 600


def test_reward_rules_are_editable_but_not_creatable(
    admin_client: Client, rule: Any
) -> None:
    change = reverse("admin:wallet_rewardrule_change", args=[rule.pk])

    assert admin_client.get(change).status_code == 200
    assert admin_client.get(reverse("admin:wallet_rewardrule_add")).status_code == 403


def test_wallet_settings_singleton_page_loads(admin_client: Client) -> None:
    WalletSettings.get_solo()

    response = admin_client.get(
        reverse("admin:wallet_walletsettings_changelist"), follow=True
    )

    assert response.status_code == 200
    assert b"credits_per_currency" in response.content


def test_a_wallet_form_can_never_write_the_counters_back(
    admin_client: Client, user: Any
) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="admin:stale")
    wallet = Wallet.objects.get(user=user)
    url = reverse("admin:wallet_wallet_change", args=[wallet.pk])

    response = admin_client.post(url, {"balance": "999999", "held": "0"})

    assert response.status_code == 403
    assert Wallet.objects.get(pk=wallet.pk).balance == 50


def test_request_records_change_only_through_their_actions(
    admin_client: Client, user: Any
) -> None:
    topup = services.request_topup(user, 100)

    response = admin_client.post(
        reverse("admin:wallet_topuprequest_change", args=[topup.pk]),
        {"status": "paid"},
    )

    assert response.status_code == 403
    topup.refresh_from_db()
    assert topup.status == TopUpRequest.Status.PENDING
    assert services.balance(user) == 0


def test_wallet_page_pages_the_ledger_instead_of_listing_every_row(
    admin_client: Client, user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wallet_admin.WalletLedgerInline, "per_page", 3)
    for number in range(1, 6):
        services.credit(
            user,
            5,
            reason=LedgerReason.TOPUP,
            key=f"admin:row:{number}",
            note=f"marker-{number}",
        )
    wallet = Wallet.objects.get(user=user)

    page = admin_client.get(reverse("admin:wallet_wallet_change", args=[wallet.pk]))

    assert page.status_code == 200
    body = page.content.decode()
    assert all(f"marker-{number}" in body for number in (3, 4, 5))
    assert not any(f"marker-{number}" in body for number in (1, 2))


def test_repair_records_are_read_only_in_the_admin(
    admin_client: Client, user: Any, staff: Any
) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="admin:repair")
    wallet = Wallet.objects.get(user=user)
    with ledger_posting():
        Wallet.objects.filter(pk=wallet.pk).update(balance=60)
    record = reconcile.repair_wallet(
        wallet.pk, actor=staff, reason_text="Counter drift", key="admin:repair:1"
    )

    detail = admin_client.get(
        reverse("admin:wallet_walletrepair_change", args=[record.pk])
    )
    add = admin_client.get(reverse("admin:wallet_walletrepair_add"))
    delete = admin_client.post(
        reverse("admin:wallet_walletrepair_delete", args=[record.pk]), {"post": "yes"}
    )

    assert detail.status_code == 200
    assert add.status_code == 403
    assert delete.status_code == 403
    assert WalletRepair.objects.filter(pk=record.pk).exists()


def test_the_ledger_changelist_does_not_query_per_row(
    admin_client: Client, make_user: Any
) -> None:
    url = reverse("admin:wallet_ledgerentry_changelist")

    def count_queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            assert admin_client.get(url).status_code == 200
        return len(captured)

    members = [make_user(f"member{number}") for number in range(6)]
    for number, member in enumerate(members[:2]):
        services.credit(member, 5, reason=LedgerReason.TOPUP, key=f"q:{number}")
    count_queries()  # warms the permission and content-type caches
    few = count_queries()
    for number, member in enumerate(members[2:], start=2):
        services.credit(member, 5, reason=LedgerReason.TOPUP, key=f"q:{number}")

    assert count_queries() == few
