import unittest

from scripts.application_map import ROOT
from scripts.governance_audit import run_governance_audit


class GovernanceAuditTests(unittest.TestCase):
    def test_audit_passes_for_real_owned_target(self):
        result = run_governance_audit("apps/ads/models.py", ROOT)

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["owner_app"], "ads")
        self.assertTrue(result["required_evidence"]["owner_tests"])
        self.assertFalse(result["violations"])

    def test_audit_fails_when_target_has_no_owner(self):
        result = run_governance_audit("unknown/path.py", ROOT)

        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["violations"])

    def test_audit_flags_skipped_tests_if_present(self):
        result = run_governance_audit("templates/base.html", ROOT)

        self.assertIn("status", result)
        self.assertIn("required_evidence", result)
        self.assertIn("violations", result)
