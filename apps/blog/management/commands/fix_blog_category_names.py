"""Fix blog category names: remove "Firmware" from brand categories, remove
brand prefix from model categories, and migrate legacy slugs.

Safe to run multiple times — idempotent.

Usage::

    python manage.py fix_blog_category_names --settings=app.settings_dev
    python manage.py fix_blog_category_names --dry-run --settings=app.settings_dev
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.blog.models import Category
from apps.firmwares.models import Brand, Model


class Command(BaseCommand):
    help = (
        "Clean up blog category names: brand categories become just the brand name, "
        "model categories become just the model name (no brand prefix, no 'Firmware')."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print changes without saving.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = bool(options["dry_run"])
        fixed_brands = 0
        fixed_models = 0

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved"))

        # ── Phase 1: Fix brand categories ──────────────────────────────
        self.stdout.write("\n── Fixing brand categories ──")
        for brand in Brand.objects.all().order_by("name"):
            clean_slug = brand.slug

            # Find the brand category by any known slug variant
            cat = (
                Category.objects.filter(slug=clean_slug).first()
                or Category.objects.filter(slug=f"{brand.slug}-firmware").first()
                or Category.objects.filter(slug=f"brand-{brand.slug}").first()
            )
            if cat is None:
                continue

            changes = []
            if cat.name != brand.name:
                changes.append(f"name: '{cat.name}' → '{brand.name}'")
                cat.name = brand.name
            if cat.slug != clean_slug:
                changes.append(f"slug: '{cat.slug}' → '{clean_slug}'")
                cat.slug = clean_slug

            if changes:
                self.stdout.write(f"  {brand.name}: {', '.join(changes)}")
                if not dry_run:
                    cat.save(update_fields=["name", "slug"])
                fixed_brands += 1

        # ── Phase 2: Fix model categories ──────────────────────────────
        self.stdout.write("\n── Fixing model categories ──")
        for model in Model.objects.select_related("brand").order_by(
            "brand__name", "name"
        ):
            canonical_slug = slugify(f"{model.brand.slug}-{model.slug}")

            # Model display name — no brand prefix
            clean_name = model.name
            if (
                hasattr(model, "marketing_name")
                and model.marketing_name
                and model.marketing_name != model.name
            ):
                # Strip brand prefix from marketing name
                mkt_clean = model.marketing_name
                brand_prefix = f"{model.brand.name} "
                if mkt_clean.startswith(brand_prefix):
                    mkt_clean = mkt_clean[len(brand_prefix) :]
                if mkt_clean != model.name:
                    clean_name = f"{model.name} ({mkt_clean})"

            cat = (
                Category.objects.filter(slug=canonical_slug).first()
                or Category.objects.filter(slug=f"model-{model.slug}").first()
            )
            if cat is None:
                continue

            # Ensure parent is the brand category
            brand_cat = Category.objects.filter(slug=model.brand.slug).first()

            changes = []
            if cat.name != clean_name:
                changes.append(f"name: '{cat.name}' → '{clean_name}'")
                cat.name = clean_name
            if cat.slug != canonical_slug:
                changes.append(f"slug: '{cat.slug}' → '{canonical_slug}'")
                cat.slug = canonical_slug
            if brand_cat and cat.parent != brand_cat:
                old_parent = cat.parent.name if cat.parent else "None"
                changes.append(f"parent: '{old_parent}' → '{brand_cat.name}'")
                cat.parent = brand_cat

            if changes:
                self.stdout.write(
                    f"  {model.brand.name} / {clean_name}: {', '.join(changes)}"
                )
                if not dry_run:
                    cat.save(update_fields=["name", "slug", "parent"])
                fixed_models += 1

        # ── Summary ────────────────────────────────────────────────────
        self.stdout.write("")
        action = "Would fix" if dry_run else "Fixed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {fixed_brands} brand categories, "
                f"{fixed_models} model categories."
            )
        )
