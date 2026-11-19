"""Management command: read-only comparison of wallet counters with the ledger."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.wallet.reconcile import DEFAULT_BATCH_SIZE, log_report, reconcile


class Command(BaseCommand):
    help = (
        "Compare every wallet's balance and held credits with its ledger and open "
        "escrow holds. Read-only: it reports drift and never changes money (use "
        "repair_wallet for an explicit, audited repair)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--wallet", type=int, action="append", help="Only this wallet id (repeat)."
        )
        parser.add_argument(
            "--deep",
            action="store_true",
            help="Also walk each ledger and verify every balance_after (slower).",
        )
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
        parser.add_argument(
            "--fail-on-drift",
            action="store_true",
            help="Exit non-zero when any drift is found (for cron and CI).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        report = reconcile(
            wallet_ids=options["wallet"],
            batch_size=options["batch_size"],
            deep=options["deep"],
        )
        log_report(report)
        self.stdout.write(
            f"Checked {report.wallets_checked} wallet(s): "
            f"{len(report.drifts)} drift(s)."
        )
        for drift in report.drifts:
            self.stdout.write(
                f"  wallet {drift.wallet_id} (user {drift.user_id}): {drift.field} "
                f"should be {drift.expected} but is {drift.actual}"
            )
        if report.drifts and options["fail_on_drift"]:
            raise CommandError("Wallet drift found; nothing was changed.")
