from __future__ import annotations

import uuid
from xml.etree import ElementTree

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sitemaps import Sitemap
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.core.models import AppRegistry
from apps.core.utils import feature_flags
from apps.pages.models import Page
from apps.seo import models as seo_models
from apps.seo import sitemap_registry
from apps.seo.models import (
    LinkableEntity,
    Redirect,
    SEOModel,
    SEOSettings,
)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SeoSettingsAuthorityTests(TestCase):
    def test_database_settings_are_the_only_authority_for_feature_flags(self):
        self.assertTrue(feature_flags.seo_enabled())

        row = SEOSettings.get_solo()
        row.seo_enabled = False
        row.save()

        self.assertFalse(feature_flags.seo_enabled())
        self.assertFalse(hasattr(seo_models, "SeoAutomationSettings"))

    def test_app_registry_flag_disables_the_seo_api(self):
        registry = AppRegistry.get_solo()
        registry.seo_enabled = False
        registry.save()

        self.assertFalse(feature_flags.seo_enabled())

    def test_object_ids_accept_uuid_primary_keys(self):
        content_type = ContentType.objects.get_for_model(Page)
        seo = SEOModel.objects.create(
            content_type=content_type, object_id=str(uuid.uuid4())
        )

        self.assertEqual(len(seo.object_id), 36)


class SitemapRegistryTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username="seo_author",
            email="seo-author@example.com",
            password="Pass-12345!",
        )
        Post.objects.create(
            title="Indexed post",
            body="<p>Body</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
        )
        Page.objects.create(
            slug="about", title="About", content="x", status="published"
        )

    def test_default_registry_wires_every_installed_content_app(self):
        sections = sitemap_registry.get_sitemaps()

        self.assertEqual(
            set(sections),
            {
                "static",
                "pages",
                "blog",
                "brands",
                "chipsets",
                "models",
                "firmware",
                "tools",
                "tags",
            },
        )

    def test_runtime_registration_validates_and_can_be_removed(self):
        class Extra(Sitemap):
            def items(self):
                return ["/extra/"]

            def location(self, item):
                return item

        sitemap_registry.register_sitemap("extra", Extra)
        self.addCleanup(sitemap_registry.unregister_sitemap, "extra")
        self.assertIn("extra", sitemap_registry.get_sitemaps())
        with self.assertRaises(ValueError):
            sitemap_registry.register_sitemap("bad/name", Extra)

    def test_sitemap_index_lists_non_empty_sections_with_absolute_urls(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        locations = [
            node.text
            for node in ElementTree.fromstring(response.content).findall(
                "sm:sitemap/sm:loc", SITEMAP_NS
            )
        ]
        self.assertIn("http://testserver/sitemap-blog.xml", locations)
        self.assertIn("http://testserver/sitemap-pages.xml", locations)
        self.assertNotIn("http://testserver/sitemap-firmware.xml", locations)

    def test_section_sitemap_contains_published_content_only(self):
        Page.objects.create(slug="draft-page", title="Draft", content="x")

        pages = self.client.get("/sitemap-pages.xml")
        blog = self.client.get("/sitemap-blog.xml")

        self.assertContains(pages, "/pages/about/")
        self.assertNotContains(pages, "draft-page")
        self.assertContains(blog, "/blog/indexed-post/")

    def test_unknown_section_and_bad_page_are_404(self):
        self.assertEqual(self.client.get("/sitemap-nope.xml").status_code, 404)
        self.assertEqual(self.client.get("/sitemap-blog.xml?p=99").status_code, 404)
        self.assertEqual(self.client.get("/sitemap-blog.xml?p=abc").status_code, 404)

    @override_settings(SITE_URL="https://example.org")
    def test_site_url_setting_controls_the_origin(self):
        response = self.client.get("/sitemap.xml")

        self.assertContains(response, "https://example.org/sitemap-blog.xml")

    def test_sitemap_is_disabled_by_seo_settings(self):
        row = SEOSettings.get_solo()
        row.sitemap_enabled = False
        row.save()

        self.assertEqual(self.client.get("/sitemap.xml").status_code, 404)
        self.assertNotContains(self.client.get("/robots.txt"), "Sitemap:")

    def test_robots_txt_points_to_the_sitemap_and_blocks_private_areas(self):
        response = self.client.get("/robots.txt")

        self.assertEqual(response["Content-Type"], "text/plain")
        body = response.content.decode()
        self.assertIn("Disallow: /admin/", body)
        self.assertIn("Sitemap: http://testserver/sitemap.xml", body)


class RedirectTests(TestCase):
    def test_active_redirect_serves_instead_of_404(self):
        Redirect.objects.create(source="/old/", target="/new/", is_permanent=True)

        response = self.client.get("/old/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/new/")

    def test_inactive_or_unknown_redirects_stay_404_and_real_pages_win(self):
        Redirect.objects.create(source="/off/", target="/new/", is_active=False)
        Redirect.objects.create(source="/health/live/", target="/elsewhere/")

        self.assertEqual(self.client.get("/off/").status_code, 404)
        self.assertEqual(self.client.get("/health/live/").status_code, 200)

    def test_redirect_validation_rejects_unsafe_targets(self):
        for source, target in (
            ("/a/", "javascript:alert(1)"),
            ("/a/", "//evil.invalid/x"),
            ("a/", "/b/"),
            ("/a/", "/a/"),
        ):
            with (
                self.subTest(source=source, target=target),
                self.assertRaises(ValidationError),
            ):
                Redirect(source=source, target=target).full_clean()

        Redirect(source="/ok/", target="https://example.org/x").full_clean()


class SeoIntegrationTests(TestCase):
    def test_saving_a_post_registers_a_linkable_entity(self):
        author = get_user_model().objects.create_user(
            username="linker", email="linker@example.com", password="Pass-12345!"
        )

        post = Post.objects.create(
            title="Linkable post", body="<p>x</p>", author=author, summary="alpha,beta"
        )

        entity = LinkableEntity.objects.get(object_id=str(post.pk))
        self.assertEqual(entity.title, "Linkable post")
        self.assertEqual(entity.url, post.get_absolute_url())

    def test_dashboard_requires_staff(self):
        url = reverse("seo:dashboard")
        self.assertEqual(self.client.get(url).status_code, 302)

        staff = get_user_model().objects.create_user(
            username="seo_staff",
            email="seo-staff@example.com",
            password="Pass-12345!",
            is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sitemap sections")

    def test_all_models_are_registered_in_the_admin(self):
        for model in (SEOSettings, Redirect, LinkableEntity, SEOModel):
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)
