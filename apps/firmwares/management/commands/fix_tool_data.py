"""Fix chipset data, template tool assignments, and post body tool references.

Performs three passes:
  1. Populate empty Model.chipset fields using known device data
  2. Fix FlashingGuideTemplate recommended_tools M2M to be chipset-aware
  3. Replace wrong tool names in blog post bodies with correct ones
"""

from __future__ import annotations

import re
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.blog.models import Post
from apps.firmwares.models import (
    FlashingGuideTemplate,
    FlashingTool,
    Model,
)
from apps.firmwares.tool_matcher import (
    get_guide_tools_for_template,
    get_tool_list_text,
    resolve_chipset,
)


class Command(BaseCommand):
    help = "Fix chipset data, template tool assignments, and post body tool references."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without saving.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]
        label = "[DRY-RUN] " if dry_run else ""

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("  Chipset & Tool Data Fix")
        self.stdout.write("=" * 60)

        chipsets_fixed = self._fix_model_chipsets(dry_run)
        templates_fixed = self._fix_template_tools(dry_run)
        posts_fixed = self._fix_post_bodies(dry_run)

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"{label}Summary:\n"
                f"  Model chipsets populated: {chipsets_fixed}\n"
                f"  Template tools fixed:     {templates_fixed}\n"
                f"  Post bodies corrected:    {posts_fixed}"
            )
        )

    # ── Pass 1: Populate Model.chipset ──────────────────────────────

    def _fix_model_chipsets(self, dry_run: bool) -> int:
        self.stdout.write("\n── Pass 1: Populating model chipsets ──")
        models = Model.objects.select_related("brand").filter(chipset="")
        fixed = 0

        for model in models:
            brand_name = model.brand.name
            model_name = model.name
            chipset = resolve_chipset(brand_name, model_name)

            if not chipset:
                self.stdout.write(
                    self.style.WARNING(
                        f"  ? {brand_name} {model_name} — no chipset data available"
                    )
                )
                continue

            self.stdout.write(f"  + {brand_name} {model_name} → {chipset}")
            if not dry_run:
                model.chipset = chipset
                model.save(update_fields=["chipset"])
            fixed += 1

        self.stdout.write(self.style.SUCCESS(f"  ✓ {fixed} chipsets populated"))
        return fixed

    # ── Pass 2: Fix template tool assignments ───────────────────────

    def _fix_template_tools(self, dry_run: bool) -> int:
        self.stdout.write("\n── Pass 2: Fixing template tool assignments ──")
        templates = FlashingGuideTemplate.objects.select_related(
            "brand"
        ).prefetch_related("recommended_tools")
        fixed = 0

        for tmpl in templates:
            brand_name = tmpl.brand.name if tmpl.brand_id else ""  # type: ignore[attr-defined]
            guide_type = tmpl.guide_type
            chipset = tmpl.chipset_filter or ""

            # If no brand, use a generic chipset for the template
            if not chipset and brand_name:
                chipset = resolve_chipset(brand_name, "")

            # Get correct tool names for this template
            correct_names = get_guide_tools_for_template(
                guide_type, brand_name, chipset
            )

            # Find matching FlashingTool objects
            correct_tools = []
            for name in correct_names:
                tool = FlashingTool.objects.filter(name=name, is_active=True).first()
                if tool:
                    correct_tools.append(tool)

            # Check if current assignment is already correct
            current_tools = set(tmpl.recommended_tools.values_list("name", flat=True))  # type: ignore[attr-defined]
            new_tools = {t.name for t in correct_tools}

            if current_tools == new_tools:
                self.stdout.write(f"  = {tmpl} — already correct")
                continue

            old_list = ", ".join(sorted(current_tools)) or "(none)"
            new_list = ", ".join(sorted(new_tools)) or "(none)"
            self.stdout.write(f"  ~ {tmpl}")
            self.stdout.write(f"    OLD: {old_list}")
            self.stdout.write(f"    NEW: {new_list}")

            if not dry_run:
                tmpl.recommended_tools.set(correct_tools)  # type: ignore[attr-defined]
            fixed += 1

        self.stdout.write(self.style.SUCCESS(f"  ✓ {fixed} templates fixed"))
        return fixed

    # ── Pass 3: Fix post body tool references ───────────────────────

    def _fix_post_bodies(self, dry_run: bool) -> int:
        self.stdout.write("\n── Pass 3: Fixing post body tool references ──")
        posts = Post.objects.select_related("firmware_brand", "firmware_model").all()
        fixed = 0

        # Patterns to find and replace wrong tool references
        wrong_patterns = [
            # The generic "SP Flash Tool, Odin, or QFIL" inserted by old enrichment
            r"SP Flash Tool,\s*Odin,\s*or\s*QFIL",
            r"SP Flash Tool,\s*Odin,\s*(?:and|or)\s*QFIL(?:/ADB)?",
            # Seed data hardcoded tools
            r"SP Flash Tool\n-\s*ADB & Fastboot\n-\s*Odin \(Samsung only\)",
        ]

        for post in posts:
            brand_name = post.firmware_brand.name if post.firmware_brand else ""  # type: ignore[attr-defined]
            model_name = (
                post.firmware_model.name if post.firmware_model else ""  # type: ignore[attr-defined]
            )
            body = post.body or ""

            if not brand_name or not body:
                continue

            chipset = resolve_chipset(brand_name, model_name)
            correct_tools = get_tool_list_text(brand_name, chipset)
            new_body = body

            for pattern in wrong_patterns:
                new_body = re.sub(pattern, correct_tools, new_body)

            # Also fix the seed-data style bullet list (both plain & HTML-encoded)
            bullet_variants = [
                "- SP Flash Tool\n- ADB & Fastboot\n- Odin (Samsung only)",
                "- SP Flash Tool\n- ADB &amp; Fastboot\n- Odin (Samsung only)",
            ]
            for old_bullet in bullet_variants:
                if old_bullet in new_body:
                    tool_names = get_guide_tools_for_template(
                        "stock_flash", brand_name, chipset
                    )
                    amp = "&amp;" if "&amp;" in old_bullet else "&"
                    bullet_list = "\n".join(
                        f"- {name.replace('&', amp)}" for name in tool_names
                    )
                    new_body = new_body.replace(old_bullet, bullet_list)

            if new_body != body:
                title_short = (post.title or "")[:50]
                self.stdout.write(
                    f"  ~ Post #{post.pk} ({title_short}…) — {brand_name} → {correct_tools}"
                )
                if not dry_run:
                    post.body = new_body
                    post.save(update_fields=["body"])
                fixed += 1

        self.stdout.write(self.style.SUCCESS(f"  ✓ {fixed} posts corrected"))
        return fixed
