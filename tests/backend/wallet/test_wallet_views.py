"""Public wallet pages: authorization, IDOR, forms, filters, the balance chip."""

from __future__ import annotations

from typing import Any

import pytest
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.wallet import services
from apps.wallet.models import LedgerEntry, LedgerReason, TopUpRequest

pytestmark = pytest.mark.django_db

PAGES = (
    "wallet:dashboard",
    "wallet:ledger",
    "wallet:earn",
    "wallet:spend",
    "wallet:topup",
    "wallet:payouts",
)


def signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.parametrize("name", PAGES)
def test_pages_require_sign_in(client: Client, name: str) -> None:
    response = client.get(reverse(name))

    assert response.status_code == 302
    assert "/login" in response["Location"]


@pytest.mark.parametrize("name", PAGES)
def test_signed_in_users_can_open_every_page(user: Any, name: str) -> None:
    response = signed_in(user).get(reverse(name))

    assert response.status_code == 200
    assert b"Credits are an internal currency" in response.content or name in (
        "wallet:dashboard",
        "wallet:ledger",
        "wallet:earn",
        "wallet:spend",
    )


def test_dashboard_shows_balance_and_recent_activity(user: Any) -> None:
    services.credit(user, 120, reason=LedgerReason.TOPUP, key="seed")
    services.lock_escrow(user, 20, key="hold:1")

    html = signed_in(user).get(reverse("wallet:dashboard")).content.decode()

    assert ">100<" in html  # available
    assert ">20<" in html  # held
    assert "Top-up" in html


def test_ledger_only_shows_the_users_own_entries(user: Any, other: Any) -> None:
    services.credit(user, 11, reason=LedgerReason.TOPUP, key="mine")
    services.credit(other, 987, reason=LedgerReason.TOPUP, key="theirs")

    html = signed_in(user).get(reverse("wallet:ledger")).content.decode()

    assert "+11" in html
    assert "987" not in html


def test_ledger_filters_and_paginates(user: Any) -> None:
    for number in range(25):
        services.credit(user, 1, reason=LedgerReason.TOPUP, key=f"c:{number}")
    services.debit(user, 3, reason=LedgerReason.SPEND, key="d:1")
    client = signed_in(user)

    first = client.get(reverse("wallet:ledger"))
    second = client.get(reverse("wallet:ledger"), {"page": 2})
    debits = client.get(reverse("wallet:ledger"), {"direction": "debit"})

    assert first.context["page_obj"].paginator.num_pages == 2
    assert len(second.context["page_obj"].object_list) == 6
    assert [e.direction for e in debits.context["page_obj"].object_list] == ["debit"]


def test_ledger_htmx_request_returns_only_the_results_partial(user: Any) -> None:
    services.credit(user, 5, reason=LedgerReason.TOPUP, key="x")

    response = signed_in(user).get(reverse("wallet:ledger"), HTTP_HX_REQUEST="true")

    assert b"<html" not in response.content
    assert b"+5" in response.content


def test_earn_page_lists_live_rules_and_hides_disabled_ones(
    user: Any, rule: Any
) -> None:
    client = signed_in(user)
    assert b"Test contribution" in client.get(reverse("wallet:earn")).content

    rule.enabled = False
    rule.save()
    assert b"Test contribution" not in client.get(reverse("wallet:earn")).content


def test_spend_page_lists_enabled_prices(user: Any) -> None:
    services.sync_registry()

    html = signed_in(user).get(reverse("wallet:spend")).content.decode()

    assert "Test package" in html and "40 credits" in html


def test_topup_post_creates_a_request_and_redirects(user: Any) -> None:
    client = signed_in(user)

    response = client.post(reverse("wallet:topup"), {"credits": "100"})

    assert response.status_code == 302
    assert TopUpRequest.objects.filter(user=user, credits=100).count() == 1
    assert b"Top-up #" in client.get(reverse("wallet:topup")).content


def test_topup_validation_errors_stay_on_the_form(user: Any) -> None:
    response = signed_in(user).post(reverse("wallet:topup"), {"credits": "1"})

    assert response.status_code == 200
    assert b"Top up between" in response.content
    assert not TopUpRequest.objects.exists()


def test_unverified_users_cannot_request_topups(make_user: Any) -> None:
    unverified = make_user("unverified", verified=False)

    response = signed_in(unverified).post(reverse("wallet:topup"), {"credits": "100"})

    assert response.status_code == 302
    assert not TopUpRequest.objects.exists()


def test_topup_post_needs_the_csrf_token(user: Any) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    assert client.post(reverse("wallet:topup"), {"credits": "100"}).status_code == 403


def test_cancelling_your_own_topup_works_and_someone_elses_is_a_404(
    user: Any, other: Any
) -> None:
    mine = services.request_topup(user, 100)
    theirs = services.request_topup(other, 100)
    client = signed_in(user)

    forbidden = client.post(reverse("wallet:cancel_topup", args=[theirs.pk]))
    done = client.post(reverse("wallet:cancel_topup", args=[mine.pk]))

    assert forbidden.status_code == 404
    assert done.status_code == 302
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.status == TopUpRequest.Status.CANCELLED
    assert theirs.status == TopUpRequest.Status.PENDING
    assert client.get(reverse("wallet:cancel_topup", args=[mine.pk])).status_code == 405


def test_payout_page_requests_a_payout_and_reports_the_withdrawable_limit(
    user: Any,
) -> None:
    services.credit(user, 600, reason=LedgerReason.EARNING, key="sale")
    client = signed_in(user)

    too_much = client.post(
        reverse("wallet:payouts"),
        {"credits": "700", "method": "bank", "destination": "DE00"},
    )
    ok = client.post(
        reverse("wallet:payouts"),
        {"credits": "600", "method": "bank", "destination": "DE00"},
    )

    assert b"You can withdraw up to 600" in too_much.content
    assert ok.status_code == 302
    assert LedgerEntry.objects.filter(reason=LedgerReason.PAYOUT).count() == 1


def test_balance_chip_renders_for_signed_in_users_only(user: Any) -> None:
    services.credit(user, 42, reason=LedgerReason.TOPUP, key="chip")
    template = Template("{% load wallet_tags %}{% wallet_balance_chip %}")
    request = RequestFactory().get("/")

    request.user = user
    shown = template.render(Context({"request": request})).strip()
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    hidden = template.render(Context({"request": request})).strip()

    assert "42 credits" in shown
    assert hidden == ""


def test_pages_return_404_when_the_registry_flag_is_off(user: Any) -> None:
    from apps.core.models import AppRegistry

    registry = AppRegistry.get_solo()
    registry.wallet_enabled = False
    registry.save()

    assert signed_in(user).get(reverse("wallet:dashboard")).status_code == 404
