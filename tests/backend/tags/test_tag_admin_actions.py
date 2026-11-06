from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.tags.models import Tag


class TagAdminApprovalTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="tag-admin", email="tag-admin@example.test"
        )
        self.client = Client()
        self.client.force_login(self.admin_user)
        self.tag = Tag.objects.create(
            name="Suggested tag", ai_suggested=True, is_curated=False
        )
        self.url = reverse("admin:tags_tag_changelist")

    def run_action(self, action):
        return self.client.post(
            self.url,
            {"action": action, "_selected_action": [str(self.tag.pk)], "index": "0"},
        )

    def test_staff_approve_and_reject_suggested_tags(self):
        self.run_action("approve_selected")
        self.tag.refresh_from_db()
        self.assertTrue(self.tag.is_curated)
        self.assertTrue(self.tag.is_active)

        self.run_action("reject_selected")
        self.tag.refresh_from_db()
        self.assertFalse(self.tag.is_curated)
        self.assertFalse(self.tag.is_active)
