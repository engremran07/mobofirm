"""Account erasure against the real wallet contract (``erasure_blockers`` / ``anonymise_user``).

``users`` orchestrates; the wallet owns its ledger and scrubs only the personal text
around it. No mocks of the contract: the call goes through ``AppService``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

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
