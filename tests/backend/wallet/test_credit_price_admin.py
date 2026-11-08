"""Credit prices edited in the admin take effect immediately for every spender."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.wallet import services
from apps.wallet.models import CreditPrice
from tests.backend.wallet.conftest import TEST_SINK

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(staff: Any) -> Client:
    client = Client()
    client.force_login(staff)
    return client


def test_editing_a_price_in_the_change_form_is_used_by_the_next_charge(
    admin_client: Client, user: Any
) -> None:
    services.sync_registry()
    row = CreditPrice.objects.get(sink_key=TEST_SINK.key)
    assert services.price_of(TEST_SINK.key) == 40

    response = admin_client.post(
        reverse("admin:wallet_creditprice_change", args=[row.pk]),
        {"credits": "10", "enabled": "on"},
    )

    assert response.status_code == 302
    row.refresh_from_db()
    assert row.credits == 10
    assert services.price_of(TEST_SINK.key) == 10
    services.credit(user, 100, reason="topup", key="price:fund")
    entry = services.spend(user, TEST_SINK.key, key="price:spend")
    assert entry.amount == 10


def test_the_changelist_toggle_does_not_overwrite_the_credits(
    admin_client: Client,
) -> None:
    services.sync_registry()
    row = CreditPrice.objects.get(sink_key=TEST_SINK.key)
    row.credits = 10
    row.save()

    admin_client.post(
        reverse("admin:wallet_creditprice_changelist"),
        {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": str(row.pk),
            "form-0-enabled": "on",
            "_save": "Save",
        },
    )

    row.refresh_from_db()
    assert row.credits == 10
