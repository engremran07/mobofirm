import unittest

from scripts.application_map import ROOT
from scripts.governance_pack import build_governance_pack


class GovernancePackTests(unittest.TestCase):
    def test_governance_pack_includes_policy_instructions_skills_agents_and_enforcement(
        self,
    ):
        report = build_governance_pack(ROOT)

        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["docs"]["AGENTS.md"])
        self.assertTrue(report["instructions"]["django-python"])
        self.assertTrue(report["skills"]["django-feature-loop"])
        self.assertTrue(report["agents"]["django-feature-engineer"])
        self.assertTrue(report["enforcement"]["scripts/governance_audit.py"])
        self.assertFalse(report["missing"])

    def test_governance_pack_reports_missing_layers_cleanly(self):
        report = build_governance_pack(ROOT)

        self.assertIn("status", report)
        self.assertIn("missing", report)
        self.assertIn("closed_loop_definition", report)
