"""Blog freshness for SEO and editors: visible update date, JSON-LD, sitemap lastmod and
the staff-only stale-content signal (admin list, filter and dashboard)."""

from __future__ import annotations

import json
from datetime import timedelta
from xml.etree import ElementTree

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog import freshness
from apps.blog.models import BlogSettings, Post, PostStatus
from apps.core import admin_dashboard
from apps.core.app_service import AppService

SITE = "https://example.org"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
BROWSER = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"}


class FreshnessTestCase(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username="fresh-author", email="fresh-author@example.test"
        )

    def post(self, title: str, *, age_days: int, status=PostStatus.PUBLISHED) -> Post:
        created = Post.objects.create(
            title=title,
            body=f"<p>Body of {title} with enough words to be valid.</p>",
            author=self.author,
            status=status,
            publish_at=timezone.now() - timedelta(days=400)
            if status == PostStatus.PUBLISHED
            else None,
        )
        # ``update`` bypasses auto_now, so the age is exactly what the test states.
        Post.objects.filter(pk=created.pk).update(
            updated_at=timezone.now() - timedelta(days=age_days)
        )
        created.refresh_from_db()
        return created


class StaleSelectionTests(FreshnessTestCase):
    def test_default_threshold_is_the_configured_setting(self):
        self.assertEqual(freshness.stale_after_days(), 180)
        BlogSettings.objects.update_or_create(pk=1, defaults={"stale_after_days": 90})
        self.assertEqual(freshness.stale_after_days(), 90)

    def test_only_live_posts_past_the_threshold_are_stale_oldest_first(self):
        old = self.post("Old guide", age_days=400)
        older = self.post("Older guide", age_days=700)
        fresh = self.post("Fresh guide", age_days=10)
        draft = self.post("Old draft", age_days=900, status=PostStatus.DRAFT)

        stale = list(freshness.stale_posts())

        self.assertEqual(stale, [older, old])
        self.assertNotIn(fresh, stale)
        self.assertNotIn(draft, stale)
        self.assertEqual(freshness.stale_post_count(), 2)

    def test_the_threshold_is_configurable_and_applies_immediately(self):
        post = self.post("Middle aged", age_days=100)
        self.assertFalse(freshness.is_stale(post))

        BlogSettings.objects.update_or_create(pk=1, defaults={"stale_after_days": 60})

        self.assertTrue(freshness.is_stale(post))
        self.assertEqual(freshness.days_since_update(post), 100)
        self.assertEqual(list(freshness.stale_posts()), [post])

    def test_scheduled_future_posts_are_never_stale(self):
        post = self.post("Scheduled", age_days=500)
        Post.objects.filter(pk=post.pk).update(
            publish_at=timezone.now() + timedelta(days=3)
        )
        post.refresh_from_db()

        self.assertFalse(freshness.is_stale(post))
        self.assertEqual(freshness.stale_post_count(), 0)

    def test_threshold_is_validated_between_a_month_and_five_years(self):
        for bad in (0, 29, 1826):
            settings_row = BlogSettings(stale_after_days=bad)
            with self.assertRaises(ValidationError, msg=str(bad)):
                settings_row.full_clean()
        for good in (30, 180, 1825):
            BlogSettings(stale_after_days=good).full_clean()

    def test_the_public_api_contract_exposes_the_count(self):
        self.post("Old guide", age_days=400)

        counter = AppService.export("blog", "stale_post_count")

        assert counter is not None
        self.assertEqual(counter(), 1)

    def test_counters_do_not_refresh_the_update_time(self):
        post = self.post("Popular", age_days=300)
        Post.objects.filter(pk=post.pk).update(views_count=5)
        Client().get(post.get_absolute_url(), **BROWSER)

        post.refresh_from_db()

        self.assertEqual(post.views_count, 6)
        self.assertTrue(freshness.is_stale(post))


class StaffSignalTests(FreshnessTestCase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser(
            username="fresh-admin", email="fresh-admin@example.test"
        )
        self.client = Client()
        self.client.force_login(self.admin)
        self.stale = self.post("Stale flashing guide", age_days=400)
        self.fresh = self.post("Fresh flashing guide", age_days=5)
        Post.objects.filter(pk=self.stale.pk).update(views_count=7)
        Post.objects.filter(pk=self.fresh.pk).update(views_count=3)
        self.changelist = reverse("admin:blog_post_changelist")

    def test_list_marks_stale_posts_with_their_age_and_shows_views(self):
        Post.objects.filter(pk=self.fresh.pk).update(views_count=1234)

        response = self.client.get(self.changelist)

        self.assertContains(response, "Stale · 400 days")
        self.assertContains(response, "1234")
        self.assertContains(response, "Freshness")

    def test_filter_lists_only_stale_or_only_non_stale_posts(self):
        stale_only = self.client.get(f"{self.changelist}?freshness=stale")
        self.assertContains(stale_only, "Stale flashing guide")
        self.assertNotContains(stale_only, "Fresh flashing guide")

        rest = self.client.get(f"{self.changelist}?freshness=fresh")
        self.assertContains(rest, "Fresh flashing guide")
        self.assertNotContains(rest, "Stale flashing guide")

    def test_list_can_be_ordered_by_last_update_and_by_views(self):
        model_admin = admin.site._registry[Post]
        request = RequestFactory().get(self.changelist)
        request.user = self.admin
        columns = list(model_admin.get_list_display(request))
        for field in ("updated_at", "views_count"):
            for prefix in ("", "-"):
                ordering = f"{prefix}{columns.index(field) + 1}"
                response = self.client.get(self.changelist, {"o": ordering})
                self.assertEqual(response.status_code, 200, ordering)
                titles = [
                    row.title for row in response.context["cl"].result_list
                ]
                expected = list(
                    Post.objects.order_by(f"{prefix}{field}").values_list(
                        "title", flat=True
                    )
                )
                self.assertEqual(titles, expected, f"{prefix}{field}")

    def test_settings_screen_edits_the_threshold(self):
        response = self.client.get(reverse("admin:blog_blogsettings_change"))

        self.assertContains(response, "stale_after_days")

    def test_dashboard_shows_the_stale_article_count_with_a_filtered_link(self):
        metrics = admin_dashboard.compute_metrics()
        kpi = next(
            item
            for item in admin_dashboard.build_kpis(metrics, self.admin)
            if item.key == "stale_posts"
        )

        self.assertEqual(kpi.value, 1)
        self.assertTrue(kpi.url.endswith("?freshness=stale"))
        self.assertEqual(kpi.permission, "blog.view_post")

    def test_readers_never_see_the_stale_signal(self):
        html = Client().get(self.stale.get_absolute_url(), **BROWSER).content.decode()

        self.assertNotIn("Stale ·", html)
        self.assertNotIn("freshness", html.lower())
        self.assertIn("Updated", html)


@override_settings(SITE_URL=SITE)
class PublicFreshnessTests(FreshnessTestCase):
    def test_article_shows_last_updated_with_a_machine_readable_time(self):
        post = self.post("Visible update", age_days=3)

        html = self.client.get(post.get_absolute_url(), **BROWSER).content.decode()

        self.assertIn("Updated", html)
        self.assertIn(f'<time datetime="{post.updated_at.isoformat()}"', html)

    def test_listing_cards_show_views_and_last_update(self):
        post = self.post("Card stats", age_days=3)
        Post.objects.filter(pk=post.pk).update(views_count=2500)

        html = self.client.get(reverse("blog:post_list")).content.decode()

        self.assertIn("2.5k", html)
        self.assertIn('title="2500 views"', html)
        self.assertIn("Updated", html)

    def test_json_ld_carries_published_and_modified_dates(self):
        post = self.post("Structured dates", age_days=3)
        Post.objects.filter(pk=post.pk).update(published_at=post.publish_at)
        post.refresh_from_db()

        html = self.client.get(post.get_absolute_url(), **BROWSER).content.decode()
        blocks = [
            json.loads(block.split("</script>")[0])
            for block in html.split('<script type="application/ld+json">')[1:]
        ]
        posting = next(block for block in blocks if block["@type"] == "BlogPosting")

        self.assertEqual(posting["datePublished"], post.published_at.isoformat())
        self.assertEqual(posting["dateModified"], post.updated_at.isoformat())

    def test_modified_date_moves_when_the_article_is_edited(self):
        post = self.post("Edited later", age_days=30)
        before = post.updated_at

        post.body = "<p>Rewritten with fresh information for readers.</p>"
        post.save()
        post.refresh_from_db()

        self.assertGreater(post.updated_at, before)
        html = self.client.get(post.get_absolute_url(), **BROWSER).content.decode()
        self.assertIn(post.updated_at.isoformat(), html)

    def test_sitemap_lastmod_is_the_real_update_time(self):
        post = self.post("Sitemap entry", age_days=12)

        document = ElementTree.fromstring(self.client.get("/sitemap-blog.xml").content)
        entry = next(
            url
            for url in document.findall("sm:url", NS)
            if (url.findtext("sm:loc", namespaces=NS) or "").endswith(
                post.get_absolute_url()
            )
        )

        lastmod = entry.findtext("sm:lastmod", namespaces=NS) or ""
        self.assertTrue(lastmod.startswith(post.updated_at.date().isoformat()), lastmod)
