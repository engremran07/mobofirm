"""Create the missing editor profiles from the code defaults (idempotent)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.site_settings.editor_models import EditorProfile


class Command(BaseCommand):
    """Seed one ``EditorProfile`` per content context."""

    help = (
        "Create the missing editor profiles from the built-in defaults. Existing "
        "profiles are kept unless --reset is given."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Declare the optional ``--reset`` switch."""
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Overwrite existing profiles with the built-in defaults.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Seed the profiles and report what happened per context."""
        outcome = EditorProfile.seed(reset=bool(options["reset"]))
        for context, state in outcome.items():
            self.stdout.write(f"{context}: {state}")
        changed = sum(1 for state in outcome.values() if state != "kept")
        self.stdout.write(self.style.SUCCESS(f"Editor profiles: {changed} changed."))
