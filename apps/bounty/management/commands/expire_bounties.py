from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.bounty.services import expire_due


class Command(BaseCommand):
    help = "Refund and expire open bounties that passed their deadline."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(f"Expired {expire_due()} bounty(ies).")
