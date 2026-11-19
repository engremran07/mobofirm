"""Run the confirmed account erasures that are due (the periodic task, on demand)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.users.services.erasure import run_due_erasures


class Command(BaseCommand):
    help = (
        "Execute scheduled account erasures whose grace period has ended. "
        "Idempotent; prints counts only."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options) -> None:
        outcome = run_due_erasures(limit=options["limit"])
        self.stdout.write(
            ", ".join(f"{name}={count}" for name, count in outcome.items())
        )
