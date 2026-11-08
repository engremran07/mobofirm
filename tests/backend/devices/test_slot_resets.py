"""Device slot resets: staff, credits and rewarded-ad allowances, audited and idempotent."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.devices import credits as wallet_credits
from apps.devices import slots
from apps.devices.admin import DeviceAdmin, DeviceSlotRequestAdmin
from apps.devices.models import (
    Device,
    DeviceConfig,
    DeviceEvent,
    DeviceResetAllowance,
    DeviceSlotRequest,
    DeviceSlotReset,
)
from apps.devices.services import get_active_device_count
from apps.users.models import Notification
from apps.wallet import api as wallet
from apps.wallet.models import CreditPrice, LedgerEntry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def economy() -> None:
    from apps.wallet.services import sync_registry

    sync_registry()


@pytest.fixture(name="funded")
def funded_fixture(user):
    wallet.credit(user, 500, reason=wallet.LedgerReason.ADJUSTMENT, key="seed-slot")
    return user


@pytest.fixture(name="full_account")
def full_account_fixture(user, approve_browsers):
    """An account at the default limit of three approved browsers."""
    return approve_browsers(user, 3)


def active(user) -> int:
    return get_active_device_count(user)


class TestStaffReset:
    @pytest.mark.usefixtures("full_account")
    def test_releases_every_approved_browser_without_deleting_history(
        self, user, staff
    ):
        reset = slots.reset_slots_as_staff(user, actor=staff, reason="Lost laptop")

        assert (reset.method, reset.devices_released, reset.actor) == (
            "staff",
            3,
            staff,
        )
        assert active(user) == 0
        assert Device.objects.filter(user=user).count() == 3
        assert Device.objects.filter(user=user, revoked_at__isnull=False).count() == 0

    @pytest.mark.usefixtures("full_account")
    def test_every_reset_is_audited_as_device_events_with_the_method(self, user, staff):
        reset = slots.reset_slots_as_staff(user, actor=staff, reason="Lost laptop")

        events = DeviceEvent.objects.filter(user=user, event_type="slot_reset")
        assert events.count() == 4
        assert {event.reason for event in events} == {"slot_reset:staff"}
        summary = events.get(device__isnull=True)
        assert summary.metadata["method"] == "staff"
        assert summary.metadata["actor_id"] == staff.pk
        assert summary.metadata["reset_id"] == reset.pk
        assert len(summary.metadata["released_device_ids"]) == 3

    @pytest.mark.usefixtures("full_account")
    def test_requires_the_reset_permission(self, user, other):
        with pytest.raises(PermissionDenied):
            slots.reset_slots_as_staff(user, actor=other, reason="please")

        assert active(user) == 3

    @pytest.mark.usefixtures("full_account")
    def test_a_staff_member_with_only_the_permission_may_reset(self, user, make_user):
        helper = make_user("perm_helper")
        helper.user_permissions.add(
            Permission.objects.get(codename="reset_device_slots")
        )

        reset = slots.reset_slots_as_staff(user, actor=helper, reason="ok")

        assert reset.actor == helper

    @pytest.mark.usefixtures("full_account")
    def test_a_reason_is_required(self, user, staff):
        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_as_staff(user, actor=staff, reason="  ")

        assert error.value.code == "reason_required"
        assert active(user) == 3

    def test_nothing_to_reset_is_refused(self, user, staff):
        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_as_staff(user, actor=staff, reason="x")

        assert error.value.code == "nothing_to_reset"
        assert not DeviceSlotReset.objects.exists()

    def test_same_key_is_idempotent_and_not_counted_in_the_monthly_cap(
        self, user, staff, approve_browsers
    ):
        approve_browsers(user, 2)
        first = slots.reset_slots_as_staff(user, actor=staff, reason="a", key="k-1")
        approve_browsers(user, 1, prefix="again")
        again = slots.reset_slots_as_staff(user, actor=staff, reason="a", key="k-1")

        assert again.pk == first.pk
        assert active(user) == 1
        assert DeviceSlotReset.objects.count() == 1
        assert slots.self_service_resets_used(user) == 0

    def test_a_key_cannot_be_replayed_against_another_account(
        self, user, other, staff, approve_browsers
    ):
        approve_browsers(user, 1)
        approve_browsers(other, 1, prefix="o")
        slots.reset_slots_as_staff(user, actor=staff, reason="a", key="shared")

        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_as_staff(other, actor=staff, reason="a", key="shared")

        assert error.value.code == "key_conflict"
        assert active(other) == 1

    @pytest.mark.usefixtures("full_account")
    def test_the_user_is_notified(self, user, staff):
        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            slots.reset_slots_as_staff(user, actor=staff, reason="x")

        assert Notification.objects.filter(
            recipient=user, title="Browser slots reset"
        ).exists()


class TestCreditsReset:
    @pytest.mark.usefixtures("full_account")
    def test_pays_the_sink_price_and_releases_the_browsers(self, funded):
        price = wallet_credits.price()
        assert price == 10

        reset = slots.reset_slots_with_credits(funded, key="a" * 32)

        assert (reset.method, reset.credits_spent) == ("credits", 10)
        assert active(funded) == 0
        assert wallet.balance(funded) == 490
        entry = LedgerEntry.objects.get(key=reset.ledger_key)
        assert (entry.amount, entry.event_key) == (10, "device.slot_reset")

    def test_the_same_key_never_charges_or_resets_twice(self, funded, approve_browsers):
        approve_browsers(funded, 3)
        first = slots.reset_slots_with_credits(funded, key="b" * 32)
        approve_browsers(funded, 2, prefix="later")

        again = slots.reset_slots_with_credits(funded, key="b" * 32)

        assert again.pk == first.pk
        assert wallet.balance(funded) == 490
        assert active(funded) == 2
        assert DeviceSlotReset.objects.count() == 1

    @pytest.mark.usefixtures("full_account")
    def test_insufficient_credits_charge_and_release_nothing(self, user):
        wallet.credit(user, 9, reason=wallet.LedgerReason.ADJUSTMENT, key="tiny")

        with pytest.raises(slots.InsufficientCreditsError):
            slots.reset_slots_with_credits(user, key="c" * 32)

        assert active(user) == 3
        assert wallet.balance(user) == 9
        assert not DeviceSlotReset.objects.exists()
        assert not DeviceEvent.objects.filter(event_type="slot_reset").exists()

    def test_nothing_to_reset_does_not_charge(self, funded):
        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_credits(funded, key="d" * 32)

        assert error.value.code == "nothing_to_reset"
        assert wallet.balance(funded) == 500

    def test_the_monthly_cap_blocks_the_third_paid_reset(
        self, funded, approve_browsers
    ):
        for index in range(2):
            approve_browsers(funded, 1, prefix=f"r{index}")
            slots.reset_slots_with_credits(funded, key=f"{index}" * 32)
        approve_browsers(funded, 1, prefix="r9")

        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_credits(funded, key="9" * 32)

        assert error.value.code == "monthly_cap"
        assert wallet.balance(funded) == 480
        assert slots.self_service_resets_used(funded) == 2

    def test_staff_resets_do_not_use_up_the_cap(self, funded, staff, approve_browsers):
        for index in range(3):
            approve_browsers(funded, 1, prefix=f"s{index}")
            slots.reset_slots_as_staff(funded, actor=staff, reason="x")
        approve_browsers(funded, 1, prefix="s9")

        slots.reset_slots_with_credits(funded, key="e" * 32)

        assert slots.self_service_resets_used(funded) == 1

    def test_last_months_resets_do_not_count(self, funded, approve_browsers):
        approve_browsers(funded, 1)
        for index in range(2):
            old = DeviceSlotReset.objects.create(
                user=funded,
                method="credits",
                key=f"old-{index}",
                devices_released=1,
            )
            DeviceSlotReset.objects.filter(pk=old.pk).update(
                created_at=timezone.now() - timedelta(days=45)
            )

        reset = slots.reset_slots_with_credits(funded, key="f" * 32)

        assert reset.devices_released == 1

    @pytest.mark.usefixtures("full_account")
    def test_a_zero_cap_switches_self_service_off(self, funded):
        DeviceConfig.objects.update_or_create(
            pk=1, defaults={"slot_reset_monthly_cap": 0}
        )

        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_credits(funded, key="g" * 32)

        assert error.value.code == "resets_disabled"

    @pytest.mark.usefixtures("full_account")
    def test_a_disabled_sink_reports_credits_unavailable(self, funded):
        CreditPrice.objects.filter(sink_key="device.slot_reset").update(enabled=False)

        assert wallet_credits.price() is None
        with pytest.raises(slots.CreditsUnavailable):
            slots.reset_slots_with_credits(funded, key="h" * 32)

        assert active(funded) == 3
        assert wallet.balance(funded) == 500

    def test_another_users_key_is_refused(self, funded, other, approve_browsers):
        wallet.credit(other, 100, reason=wallet.LedgerReason.ADJUSTMENT, key="seed-o")
        approve_browsers(funded, 1)
        approve_browsers(other, 1, prefix="o")
        slots.reset_slots_with_credits(funded, key="i" * 32)

        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_credits(other, key="i" * 32)

        assert error.value.code == "key_conflict"
        assert active(other) == 1
        assert wallet.balance(other) == 100


class TestRewardedAdAllowance:
    def test_granting_is_idempotent_per_earning_event(self, user):
        first = slots.grant_reset_allowance(
            user, key="ads:view:1", source="rewarded_ad"
        )
        again = slots.grant_reset_allowance(
            user, key="ads:view:1", source="rewarded_ad"
        )

        assert again.pk == first.pk
        assert slots.unused_allowances(user) == 1

    def test_a_key_belongs_to_one_account(self, user, other):
        slots.grant_reset_allowance(user, key="ads:view:2", source="rewarded_ad")

        with pytest.raises(slots.SlotResetError):
            slots.grant_reset_allowance(other, key="ads:view:2", source="rewarded_ad")

    @pytest.mark.usefixtures("full_account")
    def test_spending_it_releases_the_browsers_once(self, user):
        slots.grant_reset_allowance(user, key="ads:view:3", source="rewarded_ad")

        reset = slots.reset_slots_with_allowance(user, key="j" * 32)

        assert reset.method == "rewarded_ad"
        assert reset.allowance_key == "ads:view:3"
        assert active(user) == 0
        assert slots.unused_allowances(user) == 0
        event = DeviceEvent.objects.get(
            user=user, event_type="slot_reset", device__isnull=True
        )
        assert event.metadata["method"] == "rewarded_ad"

    def test_replaying_the_same_key_does_not_spend_a_second_allowance(
        self, user, approve_browsers
    ):
        approve_browsers(user, 2)
        slots.grant_reset_allowance(user, key="ads:view:4", source="rewarded_ad")
        slots.grant_reset_allowance(user, key="ads:view:5", source="rewarded_ad")
        first = slots.reset_slots_with_allowance(user, key="k" * 32)

        again = slots.reset_slots_with_allowance(user, key="k" * 32)

        assert again.pk == first.pk
        assert slots.unused_allowances(user) == 1

    @pytest.mark.usefixtures("full_account")
    def test_without_an_allowance_nothing_happens(self, user):
        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_allowance(user, key="l" * 32)

        assert error.value.code == "no_allowance"
        assert active(user) == 3

    @pytest.mark.usefixtures("full_account")
    def test_an_expired_allowance_is_not_usable(self, user):
        allowance = slots.grant_reset_allowance(
            user, key="ads:view:6", source="rewarded_ad"
        )
        DeviceResetAllowance.objects.filter(pk=allowance.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_allowance(user, key="m" * 32)

        assert error.value.code == "no_allowance"

    def test_ad_resets_share_the_monthly_cap_with_credit_resets(
        self, funded, approve_browsers
    ):
        for index in range(2):
            approve_browsers(funded, 1, prefix=f"a{index}")
            slots.grant_reset_allowance(
                funded, key=f"ads:view:cap{index}", source="rewarded_ad"
            )
            slots.reset_slots_with_allowance(funded, key=f"{index}" * 32)
        approve_browsers(funded, 1, prefix="a9")
        slots.grant_reset_allowance(funded, key="ads:view:cap9", source="rewarded_ad")

        with pytest.raises(slots.SlotResetError) as error:
            slots.reset_slots_with_allowance(funded, key="9" * 32)

        assert error.value.code == "monthly_cap"
        assert slots.unused_allowances(funded) == 1


class TestAskStaff:
    def test_a_user_has_one_open_request_and_staff_are_notified_once(self, user, staff):
        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            first, created = slots.request_staff_reset(user, "please")
            again, created_again = slots.request_staff_reset(user, "again")

        assert (created, created_again) == (True, False)
        assert again.pk == first.pk
        assert (
            Notification.objects.filter(
                recipient=staff, title="Device slot reset requested"
            ).count()
            == 1
        )

    @pytest.mark.usefixtures("full_account")
    def test_resolving_with_a_reset_releases_the_browsers_and_closes_it(
        self, user, staff
    ):
        row, _ = slots.request_staff_reset(user, "stuck")

        done = slots.resolve_slot_request(row, actor=staff, reset=True, note="Done")

        assert done.status == DeviceSlotRequest.Status.RESOLVED
        assert done.reset is not None
        assert (done.resolved_by, done.reset.method) == (staff, "staff")
        assert active(user) == 0

    @pytest.mark.usefixtures("full_account")
    def test_declining_keeps_the_browsers_and_notifies_the_user(self, user, staff):
        row, _ = slots.request_staff_reset(user)

        with patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
            done = slots.resolve_slot_request(
                row, actor=staff, reset=False, note="Use credits instead"
            )

        assert done.status == DeviceSlotRequest.Status.DECLINED
        assert active(user) == 3
        assert Notification.objects.filter(
            recipient=user, message="Use credits instead"
        ).exists()

    @pytest.mark.usefixtures("full_account")
    def test_a_request_is_answered_only_once(self, user, staff):
        row, _ = slots.request_staff_reset(user)
        slots.resolve_slot_request(row, actor=staff, reset=True, note="ok")

        with pytest.raises(slots.SlotResetError) as error:
            slots.resolve_slot_request(row, actor=staff, reset=True, note="ok")

        assert error.value.code == "already_resolved"
        assert DeviceSlotReset.objects.count() == 1

    @pytest.mark.usefixtures("full_account")
    def test_only_permitted_staff_may_answer(self, user, other):
        row, _ = slots.request_staff_reset(user)

        with pytest.raises(PermissionDenied):
            slots.resolve_slot_request(row, actor=other, reset=True, note="ok")

    @pytest.mark.usefixtures("full_account")
    def test_a_new_request_may_follow_a_closed_one(self, user, staff):
        row, _ = slots.request_staff_reset(user)
        slots.resolve_slot_request(row, actor=staff, reset=False, note="no")

        _, created = slots.request_staff_reset(user)

        assert created is True


class TestSlotOptions:
    @pytest.mark.usefixtures("full_account")
    def test_reports_the_three_ways_to_free_a_slot(self, funded):
        options = slots.slot_options(funded)

        assert options.at_limit
        assert (options.active, options.limit) == (3, 3)
        assert options.credits_price == 10
        assert options.balance == 500
        assert options.can_pay
        assert options.missing_credits == 0
        assert (options.resets_used, options.resets_cap, options.resets_left) == (
            0,
            2,
            2,
        )
        assert options.allowances == 0
        assert options.rewarded is None

    @pytest.mark.usefixtures("full_account")
    def test_missing_credits_are_reported(self, user):
        options = slots.slot_options(user)

        assert options.missing_credits == 10

    @pytest.mark.usefixtures("full_account")
    def test_an_offer_from_ads_is_surfaced(self, user):
        offer = {
            "enabled": True,
            "name": "Reset ad",
            "url": "/ads/rewarded/reset-ad/",
            "reward_description": "Watch to earn a reset",
        }
        with patch("apps.ads.api.rewarded_offer", return_value=offer):
            options = slots.slot_options(user)

        assert options.rewarded is not None
        assert options.rewarded.url == "/ads/rewarded/reset-ad/"


class TestPages:
    def test_the_page_lists_the_options_only_at_the_limit(
        self, client, funded, approve_browsers
    ):
        approve_browsers(funded, 2)
        client.force_login(funded)
        below = client.get(reverse("devices:my_devices"))
        assert below.status_code == 200
        assert b"Free a browser slot" not in below.content

        approve_browsers(funded, 1, prefix="third")
        full = client.get(reverse("devices:my_devices"))

        assert b"Free a browser slot" in full.content
        assert b"Pay 10 credits" in full.content
        assert b"Ask staff to reset" in full.content
        assert b"Rewarded ads are not available right now." in full.content

    @pytest.mark.usefixtures("full_account")
    def test_the_rewarded_option_links_to_the_ad_when_one_is_enabled(
        self, client, user
    ):
        offer = {
            "enabled": True,
            "name": "Reset ad",
            "url": "/ads/rewarded/reset-ad/",
            "reward_description": "",
        }
        client.force_login(user)

        with patch("apps.ads.api.rewarded_offer", return_value=offer):
            page = client.get(reverse("devices:my_devices"))

        assert b'href="/ads/rewarded/reset-ad/"' in page.content

    def test_a_pending_browser_gets_an_approve_button_when_a_slot_is_free(
        self, client, user, approve_browsers
    ):
        from apps.devices.services import hash_browser_key

        approve_browsers(user, 1)
        key = "z" * 43
        Device.objects.create(user=user, browser_key_hash=hash_browser_key(key))
        client.force_login(user)
        client.cookies["mobofirm_device"] = key

        page = client.get(reverse("devices:my_devices"))

        assert b"Approve this browser" in page.content

    def test_dialogs_need_a_signed_in_user(self, client):
        for name in ("slot_ask_staff", "slot_pay_credits", "slot_use_rewarded"):
            response = client.get(reverse(f"devices:{name}"))

            assert response.status_code == 302
            assert "login" in response["Location"]

    @pytest.mark.usefixtures("full_account")
    def test_pay_dialog_shows_price_and_balance(self, client, funded):
        client.force_login(funded)

        response = client.get(reverse("devices:slot_pay_credits"))

        assert response.status_code == 200
        body = response.content.decode()
        assert "10 credits" in body
        assert "500 credits" in body
        assert "Pay 10 credits and reset" in body

    @pytest.mark.usefixtures("full_account")
    def test_pay_dialog_without_enough_credits_links_to_earning_and_has_no_submit(
        self, client, user
    ):
        client.force_login(user)

        body = client.get(reverse("devices:slot_pay_credits")).content.decode()

        assert "You need 10 more credits" in body
        assert "Pay 10 credits and reset" not in body

    @pytest.mark.usefixtures("full_account")
    def test_paying_through_the_dialog_resets_and_redirects(self, client, funded):
        client.force_login(funded)
        page = client.get(reverse("devices:slot_pay_credits")).content.decode()
        key = page.split('name="request_key" value="')[1].split('"')[0]

        response = client.post(
            reverse("devices:slot_pay_credits"), {"request_key": key}
        )

        assert response.status_code == 302
        assert response["Location"] == reverse("devices:my_devices")
        assert active(funded) == 0
        assert wallet.balance(funded) == 490

    @pytest.mark.usefixtures("full_account")
    def test_resubmitting_the_dialog_does_not_charge_twice(self, client, funded):
        client.force_login(funded)
        key = "a1" * 16
        for _ in range(2):
            client.post(reverse("devices:slot_pay_credits"), {"request_key": key})

        assert wallet.balance(funded) == 490
        assert DeviceSlotReset.objects.filter(user=funded).count() == 1

    @pytest.mark.usefixtures("full_account")
    def test_a_malformed_key_is_rejected_in_the_dialog(self, client, funded):
        client.force_login(funded)

        response = client.post(
            reverse("devices:slot_pay_credits"), {"request_key": "../../etc"}
        )

        assert response.status_code == 200
        assert active(funded) == 3

    def test_dialog_refuses_when_slots_are_free(self, client, funded, approve_browsers):
        approve_browsers(funded, 1)
        client.force_login(funded)

        response = client.post(
            reverse("devices:slot_pay_credits"), {"request_key": "b2" * 16}
        )

        assert response.status_code == 200
        assert b"still have free slots" in response.content
        assert wallet.balance(funded) == 500

    @pytest.mark.usefixtures("full_account")
    def test_dialogs_are_rate_limited(self, client, funded):
        client.force_login(funded)

        with patch("apps.devices.dialogs.allow_action", return_value=False):
            response = client.post(
                reverse("devices:slot_pay_credits"), {"request_key": "c3" * 16}
            )

        assert b"Too many attempts" in response.content
        assert active(funded) == 3

    @pytest.mark.usefixtures("full_account")
    def test_ask_staff_dialog_files_one_request(self, client, user):
        client.force_login(user)

        response = client.post(
            reverse("devices:slot_ask_staff"), {"message": "Need a reset"}
        )

        assert response.status_code == 302
        assert DeviceSlotRequest.objects.filter(
            user=user, status="open", message="Need a reset"
        ).exists()

    @pytest.mark.usefixtures("full_account")
    def test_use_rewarded_dialog_spends_the_allowance(self, client, user):
        slots.grant_reset_allowance(user, key="ads:view:dlg", source="rewarded_ad")
        client.force_login(user)

        response = client.post(
            reverse("devices:slot_use_rewarded"), {"request_key": "d4" * 16}
        )

        assert response.status_code == 302
        assert active(user) == 0

    def test_users_cannot_act_on_another_users_slots(
        self, client, other, approve_browsers, funded
    ):
        approve_browsers(other, 3, prefix="victim")
        client.force_login(funded)
        approve_browsers(funded, 3)

        client.post(reverse("devices:slot_pay_credits"), {"request_key": "e5" * 16})

        assert active(other) == 3
        assert active(funded) == 0


class TestAdmin:
    @pytest.fixture
    def rf_request(self, staff):
        request = RequestFactory().post("/admin/")
        request.user = staff
        return request

    @pytest.mark.usefixtures("full_account")
    def test_new_admin_pages_load(self, client, staff, user):
        slots.request_staff_reset(user, "hi")
        slots.grant_reset_allowance(user, key="ads:view:adm", source="rewarded_ad")
        reset = slots.reset_slots_as_staff(user, actor=staff, reason="x")
        client.force_login(staff)

        for name in (
            "devices_deviceslotreset",
            "devices_deviceslotrequest",
            "devices_deviceresetallowance",
        ):
            assert client.get(reverse(f"admin:{name}_changelist")).status_code == 200
        assert (
            client.get(
                reverse("admin:devices_deviceslotreset_change", args=[reset.pk])
            ).status_code
            == 200
        )
        assert (
            client.get(reverse("admin:devices_deviceconfig_change")).status_code == 200
        )

    def post_request(self, who, reason: str):
        request = RequestFactory().post(
            "/admin/", {"reason": reason, "_form_submitted": "True"}
        )
        request.user = who
        return request

    @staticmethod
    def run_action(model_admin, name: str, request, **kwargs):
        """Invoke an admin action by name, as Unfold's dispatcher does."""
        return getattr(model_admin, name)(request, **kwargs)

    def test_staff_reset_action_on_a_device(self, staff, user, full_account):
        model_admin = DeviceAdmin(Device, django_admin.site)

        with patch.object(model_admin, "message_user") as notify:
            response = self.run_action(
                model_admin,
                "reset_slots",
                self.post_request(staff, "Lost phone"),
                object_id=str(full_account[0].pk),
            )

        assert response.status_code == 200
        assert response["HX-Redirect"]
        assert notify.called
        assert active(user) == 0
        assert DeviceSlotReset.objects.get(user=user).reason == "Lost phone"

    def test_the_action_needs_the_reset_permission(self, user, other, full_account):
        model_admin = DeviceAdmin(Device, django_admin.site)

        with pytest.raises(PermissionDenied):
            self.run_action(
                model_admin,
                "reset_slots",
                self.post_request(other, "x"),
                object_id=str(full_account[0].pk),
            )

        assert active(user) == 3

    @pytest.mark.usefixtures("full_account")
    def test_request_actions_decline_then_reset(self, staff, user):
        model_admin = DeviceSlotRequestAdmin(DeviceSlotRequest, django_admin.site)
        row, _ = slots.request_staff_reset(user)

        with patch.object(model_admin, "message_user"):
            self.run_action(
                model_admin,
                "decline",
                self.post_request(staff, "Not now"),
                object_id=str(row.pk),
            )

        row.refresh_from_db()
        assert row.status == DeviceSlotRequest.Status.DECLINED
        assert active(user) == 3

        second, _ = slots.request_staff_reset(user)
        with patch.object(model_admin, "message_user"):
            self.run_action(
                model_admin,
                "reset_and_close",
                self.post_request(staff, "Fine"),
                object_id=str(second.pk),
            )

        second.refresh_from_db()
        assert second.status == DeviceSlotRequest.Status.RESOLVED
        assert active(user) == 0

    def test_permission_gate_for_the_actions(self, user, rf_request):
        model_admin = DeviceAdmin(Device, django_admin.site)
        rf_request.user = user

        assert not model_admin.has_reset_device_slots_permission(rf_request)
