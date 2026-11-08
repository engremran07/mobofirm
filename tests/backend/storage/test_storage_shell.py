"""The storage status page renders inside the Unfold admin shell."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


class StorageStatusShellTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="ss-staff", email="ss-staff@example.test", is_staff=True
        )
        self.member = user_model.objects.create_user(
            username="ss-member", email="ss-member@example.test"
        )
        self.url = reverse("storage:status")

    def test_status_page_renders_in_the_admin_shell(self):
        self.client.force_login(self.staff)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/staff_page.html")
        self.assertTemplateNotUsed(response, "base.html")
        self.assertContains(response, 'class="mf-ui-title"')
        self.assertContains(response, "Storage status")
        self.assertContains(response, "Storage providers")
        self.assertContains(response, reverse("admin:index"))
        html = response.content.decode()
        for marker in ("site-container", "site-header", "bootstrap"):
            self.assertNotIn(marker, html)
        self.assertNotRegex(
            html, r'class="(?:[^"]*\s)?(?:btn|card|d-flex)(?:\s[^"]*)?"'
        )

    def test_status_page_requires_a_staff_session(self):
        anonymous = Client().get(self.url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("login", anonymous["Location"])
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 302)
