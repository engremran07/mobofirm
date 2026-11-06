from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.pages.models import Page

DIALOG = {"_form_submitted": "on"}


class PageAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin_user = get_user_model().objects.create_superuser(
            username="pages-admin", email="pages-admin@example.test"
        )
        self.client = Client()
        self.client.force_login(self.admin_user)
        self.page = Page.objects.create(
            slug="draft-page", title="Draft page", content="# Heading\n\nBody text."
        )

    def url(self, name: str) -> str:
        return reverse(f"admin:pages_page_{name}", args=[self.page.pk])

    def test_publish_and_unpublish_dialogs_change_the_status(self) -> None:
        opened = self.client.get(self.url("publish_page"))
        published = self.client.post(self.url("publish_page"), DIALOG)

        self.assertEqual(opened.status_code, 200)
        self.assertIn("HX-Redirect", published.headers)
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.Status.PUBLISHED)
        self.assertEqual(self.page.updated_by, self.admin_user)
        self.assertEqual(self.client.get(self.page.get_absolute_url()).status_code, 200)

        self.client.post(self.url("unpublish_page"), DIALOG)

        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.Status.DRAFT)
        self.assertEqual(self.client.get(self.page.get_absolute_url()).status_code, 404)

    def test_bulk_publish_and_unpublish_actions(self) -> None:
        changelist = reverse("admin:pages_page_changelist")

        self.client.post(
            changelist,
            {"action": "publish_selected", "_selected_action": [self.page.pk]},
        )
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.Status.PUBLISHED)

        self.client.post(
            changelist,
            {"action": "unpublish_selected", "_selected_action": [self.page.pk]},
        )
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.Status.DRAFT)

    def test_preview_renders_a_draft_that_the_public_site_hides(self) -> None:
        response = self.client.get(self.url("preview_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft page")
        self.assertContains(response, "Body text.")
        self.assertContains(response, "Not visible on the public site")
        self.assertEqual(self.client.get(self.page.get_absolute_url()).status_code, 404)

    def test_preview_sanitises_active_content(self) -> None:
        self.page.content = "<p>safe</p><script>alert(1)</script>"
        self.page.content_format = Page.ContentFormat.HTML
        self.page.save()

        response = self.client.get(self.url("preview_page"))

        self.assertContains(response, "safe")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_scheduled_pages_are_flagged_as_not_live(self) -> None:
        self.page.status = Page.Status.PUBLISHED
        self.page.publish_at = timezone.now() + timedelta(days=2)
        self.page.save()

        changelist = self.client.get(reverse("admin:pages_page_changelist"))
        preview = self.client.get(self.url("preview_page"))

        self.assertContains(changelist, "scheduled")
        self.assertContains(preview, "Not visible on the public site")

    def test_actions_are_refused_without_change_permission(self) -> None:
        viewer = get_user_model().objects.create_user(
            username="pages-viewer", email="pages-viewer@example.test", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_page"))
        client = Client()
        client.force_login(viewer)

        publish = client.post(self.url("publish_page"), DIALOG)
        bulk = client.post(
            reverse("admin:pages_page_changelist"),
            {"action": "publish_selected", "_selected_action": [self.page.pk]},
        )
        preview = client.get(self.url("preview_page"))

        self.assertEqual(publish.status_code, 403)
        self.assertEqual(bulk.status_code, 200)
        self.assertEqual(preview.status_code, 200)
        self.page.refresh_from_db()
        self.assertEqual(self.page.status, Page.Status.DRAFT)

    def test_saving_in_the_admin_records_the_author(self) -> None:
        response = self.client.post(
            reverse("admin:pages_page_add"),
            {
                "title": "Fresh",
                "slug": "fresh",
                "content": "text",
                "content_format": "md",
                "status": "draft",
                "access_level": "public",
                "changefreq": "weekly",
                "priority": "0.5",
                "include_in_sitemap": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        fresh = Page.objects.get(slug="fresh")
        self.assertEqual(fresh.created_by, self.admin_user)
        self.assertEqual(fresh.updated_by, self.admin_user)

    def test_unknown_page_returns_404(self) -> None:
        url = reverse("admin:pages_page_preview_page", args=[999999])

        self.assertEqual(self.client.get(url).status_code, 404)
