from __future__ import annotations

from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.firmwares.models import OfficialFirmware
from apps.pages import widgets
from apps.pages.models import Page
from apps.pages.rendering import plain_text_excerpt, render_page_content


class PageRenderingTests(TestCase):
    def test_markdown_is_converted_and_scripts_are_removed(self):
        html = render_page_content(
            "# Title\n\n<script>alert(1)</script>**bold** [x](javascript:alert(1))",
            "md",
        )

        self.assertIn('<h1 id="section-title">Title</h1>', html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html)

    def test_html_content_is_sanitised_at_render_time(self):
        html = render_page_content(
            '<p onclick="steal()">hi</p><iframe src="https://evil.invalid"></iframe>',
            "html",
        )

        self.assertEqual(html, "<p>hi</p>")

    def test_excerpt_is_plain_text_and_truncated(self):
        excerpt = plain_text_excerpt("# Heading\n\n" + "word " * 100, "md", limit=40)

        self.assertLessEqual(len(excerpt), 40)
        self.assertNotIn("<", excerpt)


class PageVisibilityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.member = user_model.objects.create_user(
            username="page_member",
            email="page-member@example.com",
            password="Pass-12345!",
        )
        self.staff = user_model.objects.create_user(
            username="page_staff",
            email="page-staff@example.com",
            password="Pass-12345!",
            is_staff=True,
        )
        self.public = Page.objects.create(
            slug="about", title="About", content="Hello **world**", status="published"
        )

    def test_published_page_renders_markdown(self):
        response = self.client.get(reverse("pages:page", kwargs={"slug": "about"}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<strong>world</strong>", html=True)

    def test_draft_scheduled_and_expired_pages_are_not_found(self):
        now = timezone.now()
        Page.objects.create(slug="draft", title="Draft", content="x")
        Page.objects.create(
            slug="later",
            title="Later",
            content="x",
            status="published",
            publish_at=now + timedelta(days=1),
        )
        Page.objects.create(
            slug="gone",
            title="Gone",
            content="x",
            status="published",
            unpublish_at=now - timedelta(minutes=1),
        )

        for slug in ("draft", "later", "gone", "missing"):
            with self.subTest(slug=slug):
                response = self.client.get(reverse("pages:page", kwargs={"slug": slug}))
                self.assertEqual(response.status_code, 404)

    def test_access_levels_hide_pages_without_leaking_existence(self):
        Page.objects.create(
            slug="members",
            title="Members",
            content="x",
            status="published",
            access_level="auth",
        )
        Page.objects.create(
            slug="internal",
            title="Internal",
            content="x",
            status="published",
            access_level="staff",
        )
        url_members = reverse("pages:page", kwargs={"slug": "members"})
        url_internal = reverse("pages:page", kwargs={"slug": "internal"})

        self.assertEqual(self.client.get(url_members).status_code, 404)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(url_members).status_code, 200)
        self.assertEqual(self.client.get(url_internal).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url_internal).status_code, 200)

    def test_index_lists_only_visible_pages(self):
        Page.objects.create(slug="hidden", title="Hidden page", content="x")

        response = self.client.get(reverse("pages:index"))

        self.assertContains(response, "About")
        self.assertNotContains(response, "Hidden page")

    def test_cms_pages_live_under_pages_prefix_and_no_home_landing_route(self):
        self.assertEqual(self.public.get_absolute_url(), "/pages/about/")
        with self.assertRaises(NoReverseMatch):
            reverse("pages:home_landing")

    def test_unpublish_must_follow_publish(self):
        now = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Page.objects.create(
                slug="bad-window",
                title="Bad",
                content="x",
                publish_at=now,
                unpublish_at=now - timedelta(days=1),
            )

    def test_admin_registration(self):
        self.assertIn(Page, admin.site._registry)


class PageWidgetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.uploader = get_user_model().objects.create_user(
            username="widget_uploader",
            email="widget-uploader@example.com",
            password="Pass-12345!",
        )

    def _firmware(self, name: str, **flags) -> OfficialFirmware:
        return OfficialFirmware.objects.create(
            original_file_name=name,
            stored_file_path=f"/firmware/{name}",
            uploader=self.uploader,
            **flags,
        )

    def test_latest_firmware_widget_only_lists_active_reviewed_files(self):
        self._firmware("public.zip", is_verified=True, is_active=True)
        self._firmware("unreviewed.zip", is_verified=False, is_active=True)
        self._firmware("inactive.zip", is_verified=True, is_active=False)

        names = [item["file_name"] for item in widgets.latest_firmwares(limit=10)]

        self.assertEqual(names, ["public.zip"])

    def test_widget_cache_is_invalidated_when_firmware_changes(self):
        firmware = self._firmware("first.zip", is_verified=True)
        self.assertEqual(len(widgets.latest_firmwares(limit=10)), 1)

        firmware.is_verified = False
        firmware.save()

        self.assertEqual(widgets.latest_firmwares(limit=10), [])
