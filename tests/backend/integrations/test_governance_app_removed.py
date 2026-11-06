from __future__ import annotations

import importlib

from django.apps import apps
from django.conf import settings
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.core.models import AppRegistry


class GovernanceAppRemovalTests(TestCase):
    def test_the_governance_django_app_is_gone(self):
        self.assertFalse(apps.is_installed("apps.governance"))
        self.assertNotIn(
            "apps.governance.apps.GovernanceConfig", settings.INSTALLED_APPS
        )
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("apps.governance")

    def test_no_governance_routes_or_admin_pages_remain(self):
        self.assertEqual(self.client.get("/governance/cases/").status_code, 404)
        with self.assertRaises(NoReverseMatch):
            reverse("admin:governance_reviewcase_changelist")

    def test_the_app_registry_no_longer_tracks_governance(self):
        field_names = {field.name for field in AppRegistry._meta.get_fields()}

        self.assertNotIn("governance_enabled", field_names)

    def test_each_moderated_domain_exposes_a_decision_surface(self):
        from apps.blog.services import workflow
        from apps.comments.services.decisions import moderate_comment
        from apps.firmwares.services import (
            approve_pending_firmware,
            reject_pending_firmware,
        )

        self.assertTrue(callable(workflow.publish))
        self.assertTrue(callable(moderate_comment))
        self.assertTrue(callable(approve_pending_firmware))
        self.assertTrue(callable(reject_pending_firmware))
