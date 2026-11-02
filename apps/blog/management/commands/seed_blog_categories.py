from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.blog.models import Category

DEFAULT_CATEGORIES: list[tuple[str, Iterable[str]]] = [
    ("AI", ["Safety", "Productivity", "Tools"]),
    ("Product", ["Updates", "Guides"]),
    ("Security", ["AppSec", "Infra", "Compliance"]),
]


class Command(BaseCommand):
    help = "Seed blog categories with optional parent/child hierarchy. Safe to run multiple times."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flatten",
            action="store_true",
            help="Create only top-level categories (no children).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        flatten = bool(options.get("flatten"))
        created = 0

        for parent_name, children in DEFAULT_CATEGORIES:
            parent_slug = slugify(parent_name)[:120]
            parent, _ = Category.objects.get_or_create(
                name=parent_name, defaults={"slug": parent_slug}
            )
            if flatten:
                continue
            for child_name in children:
                slug = slugify(f"{parent_name}-{child_name}")[:120]
                _, was_created = Category.objects.get_or_create(
                    name=child_name,
                    defaults={"slug": slug, "parent": parent},
                )
                created += int(was_created)

        self.stdout.write(self.style.SUCCESS("Category seeding complete."))
        if not flatten:
            self.stdout.write(
                self.style.SUCCESS(f"Children created this run: {created}")
            )
