"""Contract: apps.ads contributes ads.txt lines to apps.seo without a direct import."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from apps.ads import seo_providers
from apps.seo import wellknown

SEO_PACKAGE = Path(__file__).resolve().parents[3] / "apps" / "seo"


class AdsTxtContractTests(SimpleTestCase):
    def test_the_ads_app_registers_its_provider_with_the_seo_registry(self):
        self.assertIn(seo_providers.ads_txt_lines, wellknown._ads_providers["ads.txt"])
        self.assertEqual(wellknown._ads_providers["app-ads.txt"], [])

    def test_seo_never_imports_the_ads_app(self):
        offenders = [
            str(path.relative_to(SEO_PACKAGE))
            for path in SEO_PACKAGE.rglob("*.py")
            if "migrations" not in path.parts
            and any(
                line.strip().startswith(("from apps.ads", "import apps.ads"))
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        ]

        self.assertEqual(offenders, [])

    def test_unknown_files_and_malformed_lines_are_rejected(self):
        with self.assertRaises(ValueError):
            wellknown.register_ads_lines("sellers.json", list)
        self.assertIsNone(wellknown.ads_txt("sellers.json"))
        self.assertTrue(wellknown._valid_ads_line("google.com, pub-1, DIRECT, abc"))
        self.assertFalse(wellknown._valid_ads_line("google.com, pub-1"))
        self.assertFalse(wellknown._valid_ads_line("a, b, MAYBE"))
