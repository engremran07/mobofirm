"""Management command: explicit, audited repair of one drifted wallet."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.wallet.reconcile import RepairRefused, plan_repair, repair_wallet
from apps.wallet.services import WalletError


class Command(BaseCommand):
    help = (
        "Rewrite one wallet's balance and held counters from its ledger. Dry run by "
        "default; --apply needs --actor (a user holding wallet.adjust_wallet), "
        "--reason and --key, and leaves a WalletRepair audit row."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("wallet", type=int, help="Wallet id to repair.")
        parser.add_argument("--apply", action="store_true", help="Write the repair.")
        parser.add_argument(
            "--actor",
            help="Login (the user model's username field) of the staff user.",
        )
        parser.add_argument("--reason", default="", help="Why the repair is needed.")
        parser.add_argument(
            "--key", default="", help="Idempotency key; repeating it is harmless."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        wallet_id: int = options["wallet"]
        try:
            plan = plan_repair(wallet_id)
        except RepairRefused as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            f"Wallet {wallet_id}: balance {plan.previous_balance} -> "
            f"{plan.repaired_balance}, held {plan.previous_held} -> "
            f"{plan.repaired_held}."
        )
        if not options["apply"]:
            self.stdout.write("Dry run: nothing was changed. Add --apply to write it.")
            return
        user_model = get_user_model()
        actor = user_model.objects.filter(
            **{user_model.USERNAME_FIELD: options["actor"]}
        ).first()
        if actor is None:
            raise CommandError("--actor must name an existing user.")
        try:
            record = repair_wallet(
                wallet_id,
                actor=actor,
                reason_text=options["reason"],
                key=options["key"],
            )
        except (WalletError, PermissionDenied) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Repaired; audit record #{record.pk}.")
