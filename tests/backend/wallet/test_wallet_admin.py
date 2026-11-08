"""Wallet admin: every page loads and staff actions go through the services."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.wallet import services
from apps.wallet.models import (
    LedgerEntry,
    LedgerReason,
    PayoutRequest,
    TopUpRequest,
    Wallet,
    WalletSettings,
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
    "topuprequest",
    "payoutrequest",
)


@pytest.fixture
def admin_client(staff: Any) -> Client:
    client = Client()
    client.force_login(staff)
    return client


def test_every_wallet_model_is_registered_and_its_changelist_loads(
    admin_client: Client,
) -> None:
    services.sync_registry()
    registered = {
        m._meta.model_name
        for m in admin.site._registry
        if m._meta.app_label == "wallet"
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
