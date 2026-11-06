import unittest
from typing import Any, ClassVar

from scripts.application_map import ROOT, build_application_map


class ApplicationMapTests(unittest.TestCase):
    inventory: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls):
        cls.inventory = build_application_map(ROOT)

    def test_inventory_identifies_local_apps_models_routes_and_test_evidence(self):
        apps_by_label = {app["label"]: app for app in self.inventory["apps"]}

        self.assertIn("users", apps_by_label)
        self.assertIn(
            "CustomUser",
            {model["name"] for model in apps_by_label["users"]["models"]},
        )
        self.assertTrue(apps_by_label["users"]["backend_test_files"])
        self.assertTrue(
            any(route["name"] == "home" for route in self.inventory["routes"])
        )
        self.assertTrue(self.inventory["integration_test_files"])

    def test_inventory_captures_template_interactions_and_frontend_assets(self):
        interaction_records = [
            interaction
            for template in self.inventory["templates"]
            for interaction in template["interactions"]
        ]
        htmx_records = [
            interaction for interaction in interaction_records if interaction["htmx"]
        ]
        alpine_records = [
            interaction for interaction in interaction_records if interaction["alpine"]
        ]

        self.assertTrue(htmx_records)
        self.assertTrue(alpine_records)
        self.assertTrue(self.inventory["frontend_sources"]["javascript"])
        self.assertTrue(self.inventory["frontend_sources"]["stylesheets"])

    def test_inventory_marks_static_findings_as_candidates_not_runtime_proof(self):
        interpretation = self.inventory["interpretation"].lower()

        self.assertIn("not a product specification", interpretation)
        self.assertIn("or proof of runtime behavior", interpretation)

    def test_inventory_tracks_admin_and_asset_relationships(self):
        self.assertIn("admin_routes", self.inventory)
        self.assertIn("template_asset_usage", self.inventory)
        self.assertIn("frontend_asset_inventory", self.inventory)
        self.assertTrue(self.inventory["admin_routes"])
        self.assertTrue(
            any(
                entry["asset_references"]
                for entry in self.inventory["template_asset_usage"]
            )
        )

    def test_inventory_output_is_json_serializable_and_deterministic(self):
        import json

        first = json.dumps(self.inventory, sort_keys=True)
        second = json.dumps(build_application_map(ROOT), sort_keys=True)

        self.assertEqual(first, second)
