from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.blog.models import Post, PostRevision, PostStatus
from apps.firmwares.management.commands.seed_curated_catalog import Command
from apps.firmwares.models import (
    Brand,
    EngineeringFirmware,
    FlashingTool,
    FlashingToolCategory,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
    UnclassifiedFirmware,
    Variant,
)


class CuratedCatalogCommandTests(TestCase):
    def test_prebuilt_flashing_tool_categories_exist_after_migrations(self):
        self.assertEqual(
            set(FlashingToolCategory.objects.values_list("slug", flat=True)),
            {"android-platform-tools", "oem-recovery-utilities"},
        )

    def test_dry_run_does_not_write_catalog_records(self):
        output = StringIO()

        call_command("seed_curated_catalog", dry_run=True, stdout=output)

        self.assertIn("Dry run", output.getvalue())
        self.assertEqual(Brand.objects.count(), 0)
        self.assertEqual(Model.objects.count(), 0)
        self.assertEqual(FlashingTool.objects.count(), 0)
        self.assertEqual(Post.objects.count(), 0)

    def test_seed_loads_curated_catalog_and_manual_drafts_idempotently(self):
        get_user_model().objects.create_user(
            username="catalog-editor",
            email="catalog-editor@example.com",
            password="test-password",
            is_staff=True,
        )

        call_command("seed_curated_catalog", stdout=StringIO())
        first_counts = (
            Brand.objects.count(),
            Model.objects.count(),
            Variant.objects.count(),
            FlashingTool.objects.count(),
            Post.objects.count(),
        )
        call_command("seed_curated_catalog", stdout=StringIO())

        self.assertEqual(first_counts, (4, 3, 6, 3, 2))
        self.assertEqual(
            (
                Brand.objects.count(),
                Model.objects.count(),
                Variant.objects.count(),
                FlashingTool.objects.count(),
                Post.objects.count(),
            ),
            first_counts,
        )
        self.assertEqual(OfficialFirmware.objects.count(), 0)
        self.assertEqual(EngineeringFirmware.objects.count(), 0)
        self.assertEqual(ReadbackFirmware.objects.count(), 0)
        self.assertEqual(ModifiedFirmware.objects.count(), 0)
        self.assertEqual(OtherFirmware.objects.count(), 0)
        self.assertEqual(UnclassifiedFirmware.objects.count(), 0)
        self.assertEqual(
            Variant.objects.get(
                model__slug="galaxy-s24-ultra",
                slug="us-unlocked",
            ).model_code,
            "SM-S928U1",
        )
        self.assertEqual(
            set(Post.objects.values_list("status", "is_ai_generated", "noindex")),
            {(PostStatus.DRAFT, False, True)},
        )
        self.assertEqual(PostRevision.objects.count(), 2)

    def test_seed_refuses_a_nonlocal_database(self):
        connection = type(
            "Connection",
            (),
            {
                "vendor": "postgresql",
                "settings_dict": {"HOST": "db.example.invalid"},
            },
        )()

        with patch(
            "apps.firmwares.management.commands.seed_curated_catalog.connection",
            connection,
        ):
            with self.assertRaisesMessage(CommandError, "local database"):
                Command._require_local_database()

    def test_legacy_flashing_seed_cannot_publish_or_clear_records(self):
        with self.assertRaisesMessage(CommandError, "This legacy seed is disabled"):
            call_command("seed_flashing_data")

        self.assertEqual(Brand.objects.count(), 0)
        self.assertEqual(FlashingTool.objects.count(), 0)
        self.assertEqual(Post.objects.count(), 0)
