from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.blog.models import Post
from apps.firmwares.models import AutomatedBlogLink
from apps.seo import guides, scan
from apps.seo.models import Issue
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_post, make_staff

MODEL_URL = "/firmwares/nova/atlas-one/"
CHIPSET_URL = "/firmwares/chipsets/mediatek-helio-g100/"


def row(rows, label_part: str):
    return next(r for r in rows if label_part in r.label)


@override_settings(SITE_URL=SITE)
class GuideCoverageTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        scan.run_scan()

    def test_a_model_and_chipset_without_guides_are_flagged(self):
        model = row(guides.model_rows(), "Atlas One")
        chipset = row(guides.chipset_rows(), "Helio G100")

        self.assertEqual(model.status, "missing_guide")
        self.assertTrue(model.public)
        self.assertEqual(model.tools, ["Flasher"])
        self.assertEqual(chipset.status, "missing_guide")
        self.assertEqual(guides.summary()["models_missing"], 1)
        self.assertEqual(guides.summary()["chipsets_missing"], 1)
        self.assertEqual(len(guides.model_rows(only_missing=True)), 1)

    def test_guides_with_tools_are_covered(self):
        make_post(
            "how-to-flash-atlas-one",
            "How to flash the Atlas One",
            self.catalog.post.author,
            self.catalog.category,
            body="<h3>How to flash firmware on Nova Atlas One</h3><p>Firmware list.</p>",
            firmware_model=self.catalog.model,
        )

        model = row(guides.model_rows(), "Atlas One")

        self.assertEqual(model.status, "ok")
        self.assertTrue(model.guide_url.endswith("how-to-flash-atlas-one/"))
        self.assertTrue(model.lists_firmware)
        self.assertEqual(guides.model_rows(only_missing=True), [])

    def test_a_guide_without_a_supporting_tool_or_chipset_is_not_covered(self):
        make_post(
            "how-to-flash-atlas-one",
            "How to flash the Atlas One",
            self.catalog.post.author,
            self.catalog.category,
            body="<h3>How to flash firmware on Nova Atlas One</h3>",
            firmware_model=self.catalog.model,
        )
        self.catalog.tool.chipsets.clear()
        self.catalog.tool.supported_brands.clear()
        self.assertEqual(row(guides.model_rows(), "Atlas One").status, "no_tool")
        type(self.catalog.model).objects.filter(pk=self.catalog.model.pk).update(
            chipset_ref=None
        )
        self.assertEqual(row(guides.model_rows(), "Atlas One").status, "no_chipset")

    def test_generate_builds_the_model_and_chipset_guides_through_the_automation(self):
        synced = guides.generate(
            model_ids=[self.catalog.model.pk], chipset_ids=[self.catalog.chipset.pk]
        )

        self.assertEqual(synced, 2)
        self.assertEqual(AutomatedBlogLink.objects.count(), 2)
        self.assertEqual(row(guides.model_rows(), "Atlas One").status, "ok")
        chipset = row(guides.chipset_rows(), "Helio G100")
        self.assertEqual(chipset.status, "ok")
        self.assertTrue(chipset.lists_firmware)
        self.assertIn("Flasher", chipset.tools)
        body = Post.objects.get(firmware_automation_link__kind="chipset").body
        self.assertIn("How to flash firmware on", body)
        self.assertIn("Flasher", body)

    def test_generate_is_idempotent_and_never_duplicates_posts(self):
        guides.generate(model_ids=[self.catalog.model.pk])
        guides.generate(model_ids=[self.catalog.model.pk])

        self.assertEqual(AutomatedBlogLink.objects.filter(kind="model").count(), 1)
        self.assertEqual(
            Post.objects.filter(firmware_automation_link__kind="model").count(), 1
        )

    def test_missing_automation_is_reported(self):
        with mock.patch("apps.seo.guides.current_app") as app:
            app.tasks.get.return_value = None
            with self.assertRaises(guides.GuideTaskUnavailable):
                guides.generate(model_ids=[1])


@override_settings(SITE_URL=SITE)
class GuideScreenTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        scan.run_scan()
        self.admin = make_staff("boss", superuser=True)
        self.client.force_login(self.admin)

    def test_the_screen_lists_coverage_for_models_and_chipsets(self):
        models = self.client.get(reverse("seo:guides"))
        chipsets = self.client.get(reverse("seo:guides"), {"kind": "chipset"})

        self.assertContains(models, "Flash guide coverage")
        self.assertContains(models, "Nova Atlas One")
        self.assertContains(models, "No guide")
        self.assertContains(chipsets, "MediaTek Helio G100")
        self.assertContains(models, "mf-ui-header")
        missing = self.client.get(reverse("seo:guides"), {"missing": "1"})
        self.assertContains(missing, "Nova Atlas One")

    def test_generating_from_the_screen_closes_the_guide_issues(self):
        self.assertTrue(
            Issue.objects.filter(
                code="model_missing_flash_guide", status=Issue.Status.OPEN
            ).exists()
        )

        response = self.client.post(
            reverse("seo:guides_generate"),
            {"kind": "model", "pk": self.catalog.model.pk},
            follow=True,
        )

        self.assertContains(response, "Refreshed 1 guide post")
        self.assertFalse(
            Issue.objects.filter(
                code="model_missing_flash_guide", status=Issue.Status.OPEN
            ).exists()
        )
        bulk = self.client.post(
            reverse("seo:guides_generate"), {"kind": "chipset"}, follow=True
        )
        self.assertContains(bulk, "Refreshed 1 guide post")
        again = self.client.post(
            reverse("seo:guides_generate"), {"kind": "chipset"}, follow=True
        )
        self.assertContains(again, "No guides need generating")

    def test_permissions(self):
        self.client.force_login(make_staff("viewer", "view_issue"))

        self.assertEqual(self.client.get(reverse("seo:guides")).status_code, 200)
        self.assertEqual(
            self.client.post(reverse("seo:guides_generate")).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("seo:guides_generate")).status_code, 405
        )
        self.assertNotContains(
            self.client.get(reverse("seo:guides")), "Generate missing guides"
        )
