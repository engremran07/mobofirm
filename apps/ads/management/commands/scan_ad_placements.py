from __future__ import annotations

import re
from pathlib import Path

from django.core.management.base import BaseCommand
from django.template.defaultfilters import slugify

from apps.ads.models import AdPlacement


class Command(BaseCommand):
    help = "Scan templates for ad placeholders and ensure AdPlacement records exist."

    def add_arguments(self, parser):
        parser.add_argument(
            "--templates-dir",
            default="templates",
            help="Root templates directory to scan",
        )

    def handle(self, *args, **options):
        root = Path(options["templates_dir"]).resolve()
        pattern = re.compile(
            r"(?:ads:slot|<!--\s*ad-slot:|{%\s*render_ad_slot\s+['\"])(?P<name>[\w\-\s]+)(?:['\"]|)(?:\s+sizes=(?P<sizes>[\w,x]+))?(?:\s+types=(?P<types>[\w,]+))?",
            re.IGNORECASE,
        )
        created = 0
        updated = 0
        if not root.exists():
            self.stdout.write(self.style.WARNING(f"Templates dir not found: {root}"))
            return
        for path in root.rglob("*.html"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: S112
                continue
            for match in pattern.finditer(text):
                raw_name = match.group("name").strip()
                if not raw_name:
                    continue
                slug = slugify(raw_name)
                allowed_sizes = (match.group("sizes") or "").replace(" ", "")
                allowed_types = (match.group("types") or "").replace(
                    " ", ""
                ) or "banner,native,html"
                obj, created_flag = AdPlacement.objects.get_or_create(
                    slug=slug,
                    defaults={
                        "code": slug or raw_name.lower().replace(" ", "-"),
                        "name": raw_name,
                        "allowed_types": allowed_types,
                        "allowed_sizes": allowed_sizes,
                        "context": "auto",
                    },
                )
                if created_flag:
                    created += 1
                    continue

                changed = False
                if obj.name != raw_name:
                    obj.name = raw_name
                    changed = True
                if not obj.code:
                    obj.code = slug or raw_name.lower().replace(" ", "-")
                    changed = True
                if allowed_sizes and obj.allowed_sizes != allowed_sizes:
                    obj.allowed_sizes = allowed_sizes
                    changed = True
                if allowed_types and obj.allowed_types != allowed_types:
                    obj.allowed_types = allowed_types
                    changed = True
                if changed:
                    obj.save()
                    updated += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Scan complete. Created: {created}, Updated: {updated}, Total: {AdPlacement.objects.count()}"
            )
        )
