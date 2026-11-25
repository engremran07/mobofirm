"""Account erasure against the real wallet contract (``erasure_blockers`` / ``anonymise_user``).

``users`` orchestrates; the wallet owns its ledger and scrubs only the personal text
around it. No mocks of the contract: the call goes through ``AppService``.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.ads import rewarded
from apps.ads.api import anonymise_user
from apps.ads.erasure import ErasureReport
from apps.ads.models import (
    AdEvent,
    AffiliateClick,
    RewardedAdGrant,
    RewardedAdView,
)
from apps.devices.api import anonymise_user as devices_anonymise_user
from apps.devices.erasure import ErasureReport as DeviceErasureReport
from apps.devices.models import (
    Device,
    DeviceBrowserLink,
    DeviceEvent,
    DeviceResetAllowance,
    DeviceSlotRequest,
    DeviceSlotReset,
)
from apps.users.models_erasure import AccountErasure
from apps.users.services import erasure
from apps.wallet import services
from apps.wallet.models import LedgerEntry, LedgerReason, PayoutRequest, Wallet


def make_user(name: str, **fields):
    return get_user_model().objects.create_user(
        username=name, email=f"{name}@example.test", password="Pass-12345!", **fields
    )


class WalletErasureContractTests(TestCase):
    def setUp(self):
        self.user = make_user("erased-holder")
        self.operator = make_user("erasure-operator", is_staff=True, is_superuser=True)
        services.credit(self.user, 800, reason=LedgerReason.EARNING, key="e:sale")
        services.adjust(
            self.user,
            20,
            actor=self.operator,
            reason_text="Paid via holder@example.test",
            key="e:adjust",
        )

    def ledger(self) -> list[tuple]:
        return list(
            LedgerEntry.objects.filter(wallet__user=self.user)
            .order_by("id")
            .values_list("id", "direction", "reason", "amount", "balance_after")
        )

    def test_the_wallet_scrubs_personal_text_and_every_balance_stays(self):
        before = self.ledger()
        balance = services.balance_snapshot(self.user).total

        result = erasure.anonymize_account(
            self.user, actor=self.operator, reason="Subject request"
        )

        self.assertIn(
            result.status,
            {AccountErasure.Status.COMPLETED, AccountErasure.Status.PARTIAL},
        )
        self.assertIn("wallet", result.receipt["apps"])
        self.assertNotIn("wallet", result.receipt["gaps"])
        self.assertEqual(self.ledger(), before)
        self.assertEqual(services.balance_snapshot(self.user).total, balance)
        self.assertFalse(
            LedgerEntry.objects.filter(wallet__user=self.user).exclude(note="").exists()
        )
        self.assertTrue(Wallet.objects.filter(user=self.user).exists())
        self.assertIsNotNone(
            get_user_model().objects.get(pk=self.user.pk).anonymized_at
        )

    def test_an_open_payout_blocks_the_erasure_until_it_is_settled(self):
        payout = services.request_payout(
            self.user, 600, method="paypal", destination="holder@example.test"
        )

        with self.assertRaises(erasure.ErasureBlocked) as raised:
            erasure.anonymize_account(
                self.user, actor=self.operator, reason="Subject request"
            )

        self.assertIn("wallet", raised.exception.blockers)
        self.assertIsNone(get_user_model().objects.get(pk=self.user.pk).anonymized_at)
        self.assertEqual(
            PayoutRequest.objects.get(pk=payout.pk).destination, "holder@example.test"
        )

        services.approve_payout(payout.pk, staff_user=self.operator)
        services.mark_payout_paid(payout.pk, staff_user=self.operator, reference="tx 1")
        erasure.anonymize_account(
            self.user, actor=self.operator, reason="Subject request"
        )

        self.assertEqual(
            PayoutRequest.objects.get(pk=payout.pk).destination, "[erased]"
        )


class AdsErasureContractTests(TestCase):
    def setUp(self):
        self.user = make_user("ads-erased")
        self.operator = make_user(
            "ads-erasure-operator", is_staff=True, is_superuser=True
        )

    def test_ads_detaches_history_and_invalidates_active_reward_sessions(self):
        click = AffiliateClick.objects.create(
            user=self.user, page_url="https://example.test/article"
        )
        event = AdEvent.objects.create(event_type="click", user=self.user)
        active_view = RewardedAdView.objects.create(
            user=self.user,
            status=RewardedAdView.Status.STARTED,
            token_digest=rewarded.digest_token("active-token"),
        )
        completed_view = RewardedAdView.objects.create(
            user=self.user,
            status=RewardedAdView.Status.COMPLETED,
            token_digest="completed-token-digest",
        )
        grant = RewardedAdGrant.objects.create(
            user=self.user,
            view=completed_view,
            kind=RewardedAdGrant.Kind.AD_FREE,
            amount=1,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        result = erasure.anonymize_account(
            self.user, actor=self.operator, reason="Subject request"
        )

        self.assertIn("ads", result.receipt["apps"])
        self.assertNotIn("ads", result.receipt["gaps"])
        self.assertEqual(
            result.receipt["apps"]["ads"],
            {
                "affiliate_clicks": 1,
                "ad_events": 1,
                "rewarded_views": 2,
                "rewarded_grants": 1,
                "active_views_invalidated": 1,
            },
        )
        self.assertIsNone(AffiliateClick.objects.get(pk=click.pk).user_id)
        self.assertIsNone(AdEvent.objects.get(pk=event.pk).user_id)
        active_view.refresh_from_db()
        completed_view.refresh_from_db()
        grant.refresh_from_db()
        self.assertIsNone(active_view.user_id)
        self.assertEqual(active_view.status, RewardedAdView.Status.EXPIRED)
        self.assertEqual(active_view.token_digest, "")
        with self.assertRaises(rewarded.RewardedAdError) as raised:
            rewarded.complete(self.user, "active-token")
        self.assertEqual(raised.exception.code, "invalid_token")
        self.assertIsNone(completed_view.user_id)
        self.assertIsNone(grant.user_id)
        self.assertTrue(RewardedAdView.objects.filter(pk=active_view.pk).exists())
        self.assertTrue(RewardedAdGrant.objects.filter(pk=grant.pk).exists())
        self.assertEqual(anonymise_user(self.user), ErasureReport())


class DeviceErasureContractTests(TestCase):
    def setUp(self):
        self.user = make_user("devices-erased")
        self.operator = make_user(
            "devices-erasure-operator", is_staff=True, is_superuser=True
        )

    def test_device_erasure_revokes_credentials_and_preserves_audit_balances(self):
        device = Device.objects.create(
            user=self.user,
            browser_key_hash="a" * 64,
            os_credential_id="credential-id",
            os_credential_id_hash="b" * 64,
            os_credential_public_key="public-key",
        )
        browser_link = DeviceBrowserLink.objects.create(
            user=self.user, device=device, browser_key_hash="c" * 64
        )
        device_event = DeviceEvent.objects.create(
            user=self.user,
            device=device,
            event_type="login",
            ip_hash="d" * 64,
            metadata={"policy": {"risk": 3}},
        )
        request = DeviceSlotRequest.objects.create(
            user=self.user, message="Please reset my personal device."
        )
        reset = DeviceSlotReset.objects.create(
            user=self.user,
            method=DeviceSlotReset.Method.CREDITS,
            key="devices-erasure-reset",
            credits_spent=10,
            reason="Personal explanation",
        )
        allowance = DeviceResetAllowance.objects.create(
            user=self.user,
            key="devices-erasure-allowance",
            source="rewarded_ad",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        result = erasure.anonymize_account(
            self.user, actor=self.operator, reason="Subject request"
        )

        self.assertIn("devices", result.receipt["apps"])
        self.assertNotIn("devices", result.receipt["gaps"])
        self.assertEqual(
            result.receipt["apps"]["devices"],
            {
                "devices_deleted": 1,
                "browser_links_deleted": 1,
                "device_events_detached": 1,
                "slot_requests_scrubbed": 1,
                "slot_resets_scrubbed": 1,
                "reset_allowances_deleted": 1,
            },
        )
        self.assertFalse(Device.objects.filter(pk=device.pk).exists())
        self.assertFalse(DeviceBrowserLink.objects.filter(pk=browser_link.pk).exists())
        device_event.refresh_from_db()
        self.assertIsNone(device_event.user_id)
        self.assertIsNone(device_event.device_id)
        self.assertEqual(device_event.ip_hash, "")
        self.assertEqual(device_event.metadata, {})
        request.refresh_from_db()
        self.assertEqual(request.status, DeviceSlotRequest.Status.DECLINED)
        self.assertEqual(request.message, "")
        self.assertFalse(DeviceResetAllowance.objects.filter(pk=allowance.pk).exists())
        reset.refresh_from_db()
        self.assertEqual(reset.credits_spent, 10)
        self.assertEqual(reset.reason, "")
        self.assertEqual(devices_anonymise_user(self.user), DeviceErasureReport())
