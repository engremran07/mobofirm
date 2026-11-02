"""
Management command to enrich existing firmware blog posts with flashing tool
recommendations and chipset-specific flashing guides.

Targets auto-generated firmware blog posts (is_ai_generated=True) that have a
firmware_brand/firmware_model FK. Re-generates the post body via
FirmwareBlogService.generate_firmware_post() which now includes:
  - Recommended Flashing Tools section (grouped by OEM/Free/Crack)
  - Chipset-specific flashing guide steps
  - Prerequisites box

Usage:
    python manage.py enrich_firmware_blogs --settings=app.settings_dev
    python manage.py enrich_firmware_blogs --brand=samsung --settings=app.settings_dev
    python manage.py enrich_firmware_blogs --slug=alcatel-tetra-firmware-download --settings=app.settings_dev
    python manage.py enrich_firmware_blogs --dry-run --settings=app.settings_dev
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandParser

from apps.blog.models import Post
from apps.firmwares.blog_automation import FirmwareBlogService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Enrich firmware blog posts with flashing tool recommendations and guides"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--brand",
            type=str,
            default="",
            help="Only process posts for this brand slug (e.g. samsung, xiaomi)",
        )
        parser.add_argument(
            "--slug",
            type=str,
            default="",
            help="Only process a single post by slug",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which posts would be updated without making changes",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum number of posts to process (0 = all)",
        )

    def handle(self, *args, **options) -> None:  # type: ignore[override]
        brand_slug: str = options.get("brand", "") or ""
        post_slug: str = options.get("slug", "") or ""
        dry_run: bool = options.get("dry_run", False)
        limit: int = options.get("limit", 0) or 0

        # Build queryset of firmware blog posts
        qs = Post.objects.filter(
            is_ai_generated=True,
            firmware_model__isnull=False,
        ).select_related("firmware_brand", "firmware_model")

        if post_slug:
            qs = qs.filter(slug=post_slug)
        elif brand_slug:
            qs = qs.filter(firmware_brand__slug=brand_slug)

        qs = qs.order_by("firmware_brand__name", "firmware_model__name")

        if limit > 0:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(f"Found {total} firmware blog post(s) to enrich")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made"))
            for post in qs:
                brand = post.firmware_brand.name if post.firmware_brand else "?"  # type: ignore[union-attr]
                model = post.firmware_model.name if post.firmware_model else "?"  # type: ignore[union-attr]
                self.stdout.write(f"  Would update: {brand} {model} ({post.slug})")
            return

        success = 0
        skipped = 0
        errors = 0

        for post in qs:
            model_obj = post.firmware_model
            if not model_obj:
                skipped += 1
                continue
            try:
                result = FirmwareBlogService.generate_firmware_post(
                    model_obj, force_update=True
                )
                if result:
                    success += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Enriched: {model_obj.brand.name} {model_obj.name}"
                        )
                    )
                else:
                    skipped += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  - Skipped: {model_obj.brand.name} {model_obj.name}"
                        )
                    )
            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗ Error: {model_obj.brand.name} {model_obj.name} — {e}"
                    )
                )
                logger.exception("enrich_firmware_blogs error for %s", post.slug)

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 60))
        self.stdout.write(self.style.SUCCESS("Firmware Blog Enrichment Complete"))
        self.stdout.write(self.style.SUCCESS(f"Total: {total}"))
        self.stdout.write(self.style.SUCCESS(f"Enriched: {success}"))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped: {skipped}"))
        if errors:
            self.stdout.write(self.style.ERROR(f"Errors: {errors}"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
