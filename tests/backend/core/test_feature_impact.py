import unittest

from scripts.application_map import ROOT
from scripts.feature_impact import build_feature_impact_report


class FeatureImpactReportTests(unittest.TestCase):
    def test_report_uses_the_owner_app_and_related_routes(self):
        report = build_feature_impact_report("apps/ads/models.py", ROOT)

        self.assertEqual(report["owner_app"], "ads")
        self.assertIn("ads", report["related_apps"])
        self.assertTrue(report["routes"])
        self.assertTrue(report["tests"])

    def test_report_surfaces_template_and_frontend_asset_traceability(self):
        report = build_feature_impact_report("templates/base.html", ROOT)

        self.assertIn("frontend_assets", report)
        self.assertTrue(report["frontend_assets"])
        self.assertTrue(report["summary"]["asset_count"] >= 1)

    def test_report_has_stable_summary_counts(self):
        first = build_feature_impact_report("apps/users/models.py", ROOT)
        second = build_feature_impact_report("apps/users/models.py", ROOT)

        self.assertEqual(first["summary"], second["summary"])
        self.assertEqual(first["owner_app"], second["owner_app"])
