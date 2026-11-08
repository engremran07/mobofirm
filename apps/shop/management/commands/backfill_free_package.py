"""Backfill the free package for accounts that already exist (safe to re-run)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.shop.baseline import backfill_baseline_packages


class Command(BaseCommand):
    help = (
        "Give every existing account the free (default) package record. New accounts "
        "get it at sign-up; running this again changes nothing."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        result = backfill_baseline_packages()
        if result.no_default_package:
            raise CommandError(
                "There is no active default package. Run `manage.py seed_packages` "
                "or mark a package as the default in the admin first."
            )
        self.stdout.write(
            f"free package: {result.created} assigned, {result.already} already had it"
        )
