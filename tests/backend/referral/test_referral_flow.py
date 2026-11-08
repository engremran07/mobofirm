"""Referral flow: capture, sign-up link, verified-and-qualified rewards, anti-abuse."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from allauth.account.signals import user_signed_up
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.events.bus import EventTypes, event_bus
from apps.referral import services
from apps.referral.models import Referral, ReferralCode, ReferralSettings
from apps.wallet import api as wallet
from apps.wallet.models import LedgerEntry, RewardRefusal, RewardRule

pytestmark = pytest.mark.django_db


def test_codes_are_unique_per_user_and_stable(referrer: Any, make_user: Any) -> None:
    first = services.get_or_create_code(referrer)

    assert services.get_or_create_code(referrer).pk == first.pk
    assert services.get_or_create_code(make_user("other")).code != first.code
    assert len(first.code) == 8


def test_find_code_rejects_malformed_unknown_and_inactive_codes(referrer: Any) -> None:
    code = services.get_or_create_code(referrer)

    assert services.find_code(code.code.lower()) == code
    assert services.find_code("") is None
    assert services.find_code("../etc") is None
    assert services.find_code("ZZZZZZZZ") is None
    ReferralCode.objects.filter(pk=code.pk).update(is_active=False)
    assert services.find_code(code.code) is None


def test_the_landing_url_captures_the_code_and_sends_visitors_to_sign_up(
    referrer: Any,
) -> None:
    code = services.get_or_create_code(referrer)
    client = Client()

    response = client.get(reverse("referral:landing", args=[code.code]))

    assert response.status_code == 302
    assert response["Location"] == reverse("users:account_signup")
    assert client.session["referral_code"] == code.code
    code.refresh_from_db()
    assert code.clicks == 1


def test_an_unknown_landing_code_captures_nothing(client: Client) -> None:
    response = client.get(reverse("referral:landing", args=["NOPE1234"]))

    assert response.status_code == 302
    assert "referral_code" not in client.session


def test_the_ref_query_parameter_is_captured_on_any_page(
    referrer: Any, client: Client
) -> None:
    code = services.get_or_create_code(referrer)

    client.get("/", {"ref": code.code})

    assert client.session["referral_code"] == code.code


def test_capture_is_off_when_referrals_are_disabled(
    referrer: Any, make_request: Any
) -> None:
    cfg = ReferralSettings.get_solo()
    cfg.enabled = False
    cfg.save()
    request = make_request()

    assert not services.capture_code(
        request, services.get_or_create_code(referrer).code
    )


def test_owners_do_not_capture_their_own_code(referrer: Any, make_request: Any) -> None:
    request = make_request()
    request.user = referrer

    assert not services.capture_code(
        request, services.get_or_create_code(referrer).code
    )


def test_a_captured_code_expires(referrer: Any, make_request: Any) -> None:
    request = make_request()
    services.capture_code(request, services.get_or_create_code(referrer).code)
    cfg = ReferralSettings.get_solo()
    cfg.capture_days = 1
    cfg.save()
    request.session["referral_code_at"] = (
        timezone.now() - timedelta(days=2)
    ).isoformat()

    assert services.captured_code(request) == ""


def test_signing_up_links_the_invitee_once_through_the_allauth_signal(
    referrer: Any, make_user: Any, make_request: Any
) -> None:
    code = services.get_or_create_code(referrer)
    request = make_request()
    services.capture_code(request, code.code)
    invitee = make_user("newcomer", verified=False)

    user_signed_up.send(sender=type(invitee), request=request, user=invitee)
    user_signed_up.send(sender=type(invitee), request=request, user=invitee)

    referral = Referral.objects.get(invitee=invitee)
    assert (referral.referrer_id, referral.status) == (referrer.pk, "pending")
    assert Referral.objects.count() == 1
    assert "referral_code" not in request.session


def test_signing_up_without_a_code_creates_no_referral(
    make_user: Any, make_request: Any
) -> None:
    invitee = make_user("organic")

    assert services.register_signup(invitee, make_request()) is None
    assert not Referral.objects.exists()


def test_no_reward_before_email_verification(invite: Any, referrer: Any) -> None:
    invitee = invite(verified=False)

    services.record_qualifying_action(invitee, "firmware")

    assert Referral.objects.get(invitee=invitee).status == "pending"
    assert wallet.balance(referrer) == 0


def test_no_reward_until_the_first_approved_action(invite: Any, referrer: Any) -> None:
    invitee = invite(verified=True)

    services.handle_email_verified(invitee)

    referral = Referral.objects.get(invitee=invitee)
    assert referral.status == "pending"
    assert referral.email_verified_at is not None
    assert wallet.balance(referrer) == 0


def test_verified_invitee_with_an_approved_action_rewards_the_referrer_once(
    invite: Any, referrer: Any
) -> None:
    invitee = invite(verified=True)

    services.handle_email_verified(invitee)
    services.record_qualifying_action(invitee, "firmware")
    services.record_qualifying_action(invitee, "firmware")
    services.handle_email_verified(invitee)

    referral = Referral.objects.get(invitee=invitee)
    assert (referral.status, referral.reward_credits) == ("rewarded", 20)
    assert wallet.balance(referrer) == 20
    assert wallet.balance(invitee) == 5
    keys = set(LedgerEntry.objects.values_list("key", flat=True))
    assert keys == {f"referral:{invitee.pk}", f"referral-welcome:{invitee.pk}"}


def test_the_reward_amount_is_the_central_wallet_rule(
    invite: Any, referrer: Any
) -> None:
    RewardRule.objects.filter(event_key="referral.verified").update(credits=33)
    invitee = invite(verified=True)

    services.record_qualifying_action(invitee, "firmware")

    assert wallet.balance(referrer) == 33


def test_staff_can_reward_on_verification_alone(invite: Any, referrer: Any) -> None:
    cfg = ReferralSettings.get_solo()
    cfg.require_qualifying_action = False
    cfg.save()
    invitee = invite(verified=False)

    services.handle_email_verified(invitee)

    assert Referral.objects.get(invitee=invitee).status == "rewarded"
    assert wallet.balance(referrer) == 20


def test_the_contribution_event_qualifies_the_invitee(
    invite: Any, referrer: Any
) -> None:
    invitee = invite(verified=True)

    event_bus.publish(
        "contribution.approved", {"user_id": invitee.pk, "kind": "firmware.approved"}
    )

    referral = Referral.objects.get(invitee=invitee)
    assert (referral.status, referral.qualifying_kind) == (
        "rewarded",
        "firmware.approved",
    )


def test_the_email_verified_event_triggers_evaluation(
    invite: Any, referrer: Any
) -> None:
    invitee = invite(verified=True)
    Referral.objects.filter(invitee=invitee).update(qualified_at=timezone.now())

    event_bus.publish(EventTypes.USER_EMAIL_VERIFIED, {"user_id": invitee.pk})

    assert Referral.objects.get(invitee=invitee).status == "rewarded"


def test_unrelated_events_are_ignored(referrer: Any) -> None:
    event_bus.publish("contribution.approved", {"user_id": 99999})
    event_bus.publish("contribution.approved", "junk")

    assert not Referral.objects.exists()


def test_same_network_signup_is_not_rewarded(
    invite: Any, referrer: Any, make_request: Any
) -> None:
    services.summary_for(referrer, make_request(ip="198.51.100.9"))
    invitee = invite(verified=True, ip="198.51.100.9")

    services.record_qualifying_action(invitee, "firmware")

    referral = Referral.objects.get(invitee=invitee)
    assert (referral.status, referral.reject_reason) == ("rejected", "same_network")
    assert wallet.balance(referrer) == 0


def test_many_invitees_from_one_network_pay_only_the_first(
    invite: Any, referrer: Any
) -> None:
    first = invite("friend1", verified=True, ip="192.0.2.50")
    services.record_qualifying_action(first, "firmware")
    second = invite("friend2", verified=True, ip="192.0.2.50")

    services.record_qualifying_action(second, "firmware")

    assert Referral.objects.get(invitee=first).status == "rewarded"
    assert Referral.objects.get(invitee=second).reject_reason == "same_network"
    assert wallet.balance(referrer) == 20


def test_same_device_check_uses_the_devices_contract_when_it_exists(
    invite: Any, referrer: Any
) -> None:
    class Devices:
        @staticmethod
        def users_share_device(a: Any, b: Any) -> bool:
            return True

    invitee = invite(verified=True)
    with patch("apps.referral.services.AppService.get", return_value=Devices):
        services.record_qualifying_action(invitee, "firmware")

    assert Referral.objects.get(invitee=invitee).reject_reason == "same_device"
    assert wallet.balance(referrer) == 0


def test_the_lifetime_cap_per_referrer_is_enforced(invite: Any, referrer: Any) -> None:
    cfg = ReferralSettings.get_solo()
    cfg.max_rewarded_per_referrer = 1
    cfg.max_rewarded_per_referrer_per_day = 0
    cfg.block_same_network = False
    cfg.save()
    first = invite("friend1", verified=True, ip="192.0.2.1")
    second = invite("friend2", verified=True, ip="192.0.2.2")

    services.record_qualifying_action(first, "firmware")
    services.record_qualifying_action(second, "firmware")

    assert Referral.objects.get(invitee=second).reject_reason == "referrer_cap_reached"
    assert wallet.balance(referrer) == 20


def test_the_daily_cap_leaves_the_referral_pending_and_a_sweep_pays_it_later(
    invite: Any, referrer: Any
) -> None:
    cfg = ReferralSettings.get_solo()
    cfg.max_rewarded_per_referrer_per_day = 1
    cfg.block_same_network = False
    cfg.save()
    first = invite("friend1", verified=True, ip="192.0.2.1")
    second = invite("friend2", verified=True, ip="192.0.2.2")
    services.record_qualifying_action(first, "firmware")

    services.record_qualifying_action(second, "firmware")
    assert Referral.objects.get(invitee=second).status == "pending"

    Referral.objects.filter(invitee=first).update(
        rewarded_at=timezone.now() - timedelta(days=1)
    )
    assert services.sweep_pending() == 1
    assert Referral.objects.get(invitee=second).status == "rewarded"


def test_a_blocked_wallet_reward_is_recorded_and_the_referral_marked(
    invite: Any, referrer: Any
) -> None:
    RewardRule.objects.filter(event_key="referral.verified").update(enabled=False)
    invitee = invite(verified=True)

    services.record_qualifying_action(invitee, "firmware")

    referral = Referral.objects.get(invitee=invitee)
    assert (referral.status, referral.reject_reason) == ("rejected", "reward_refused")
    assert RewardRefusal.objects.filter(key=f"referral:{invitee.pk}").exists()


def test_the_database_forbids_self_referral(referrer: Any) -> None:
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        Referral.objects.create(referrer=referrer, invitee=referrer, code="X")


def test_each_user_can_be_invited_only_once(
    invite: Any, make_user: Any, make_request: Any
) -> None:
    invitee = invite()
    rival = make_user("rival")
    request = make_request()
    services.capture_code(request, services.get_or_create_code(rival).code)

    assert services.register_signup(invitee, request) is None
    assert Referral.objects.get(invitee=invitee).referrer.username == "referrer"
