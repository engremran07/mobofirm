"""View counter: bot-aware, once per session per day, atomic; and the stats tag."""

from __future__ import annotations

from datetime import timedelta

from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.templatetags.content_tags import short_number, updated_ago
from apps.core.view_counter import SESSION_KEY, is_bot_request, should_count
from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.firmwares.tracking_models import FirmwareView

BROWSER = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"}


class BotDetectionTests(TestCase):
    def request(self, method="get", **headers):
        return getattr(RequestFactory(), method)("/", **headers)

    def test_browsers_are_not_bots(self):
        self.assertFalse(is_bot_request(self.request(**BROWSER)))

    def test_crawlers_empty_agents_head_and_prefetch_are_bots(self):
        self.assertTrue(
            is_bot_request(self.request(HTTP_USER_AGENT="Googlebot/2.1"))
        )
        self.assertTrue(is_bot_request(self.request()))
        self.assertTrue(is_bot_request(self.request("head", **BROWSER)))
        self.assertTrue(
            is_bot_request(self.request(HTTP_SEC_PURPOSE="prefetch", **BROWSER))
        )
        self.assertTrue(is_bot_request(self.request(HTTP_PURPOSE="prefetch", **BROWSER)))

    def test_should_count_once_per_day_then_again_the_next_day(self):
        request = self.request(**BROWSER)
        request.session = {}

        self.assertTrue(should_count(request, "post", 1))
        self.assertFalse(should_count(request, "post", 1))
        self.assertTrue(should_count(request, "post", 2))

        request.session[SESSION_KEY]["day"] = (
            timezone.localdate() - timedelta(days=1)
        ).isoformat()
        self.assertTrue(should_count(request, "post", 1))

    def test_bots_and_sessionless_requests_never_count(self):
        bot = self.request(HTTP_USER_AGENT="bingbot")
        bot.session = {}
        self.assertFalse(should_count(bot, "post", 1))
        self.assertEqual(bot.session, {})
        self.assertFalse(should_count(self.request(**BROWSER), "post", 1))


class FirmwareViewCountTests(TestCase):
    def setUp(self):
        brand = Brand.objects.create(name="Count Brand", slug="count-brand")
        model = Model.objects.create(
            brand=brand, name="Count Phone", slug="count-phone", model_code="CC-1"
        )
        self.firmware = OfficialFirmware.objects.create(
            original_file_name="count.zip",
            stored_file_path="/firmware/count.zip",
            brand=brand,
            model=model,
            is_verified=True,
        )
        self.url = reverse(
            "firmwares:firmware_detail", args=["official", self.firmware.pk]
        )

    def views(self):
        self.firmware.refresh_from_db()
        return self.firmware.view_count

    def test_human_views_count_once_and_log_one_analytics_row(self):
        self.client.get(self.url, **BROWSER)
        self.client.get(self.url, **BROWSER)

        self.assertEqual(self.views(), 1)
        self.assertEqual(FirmwareView.objects.count(), 1)

    def test_crawlers_and_prefetch_are_not_counted(self):
        self.client.get(self.url, HTTP_USER_AGENT="Googlebot/2.1")
        self.client.get(self.url, HTTP_SEC_PURPOSE="prefetch", **BROWSER)
        self.client.get(self.url)

        self.assertEqual(self.views(), 0)
        self.assertEqual(FirmwareView.objects.count(), 0)

    def test_the_page_shows_the_exact_count_and_last_update(self):
        self.client.get(self.url, **BROWSER)

        response = self.client.get(self.url, **BROWSER)

        self.assertContains(response, 'title="1 view"')


class ContentStatsTagTests(TestCase):
    def render(self, obj):
        template = Template("{% load content_tags %}{% content_stats obj %}")
        return template.render(Context({"obj": obj}))

    def test_short_number_forms(self):
        self.assertEqual(
            [short_number(n) for n in (0, 950, 1000, 1234, 12_000, 3_400_000)],
            ["0", "950", "1k", "1.2k", "12k", "3.4M"],
        )

    def test_updated_ago_uses_the_largest_unit(self):
        self.assertEqual(updated_ago(None), "")
        self.assertEqual(updated_ago(timezone.now()), "just now")
        self.assertEqual(
            updated_ago(timezone.now() - timedelta(days=3, hours=2)), "3\xa0days ago"
        )

    def test_renders_views_downloads_and_a_machine_readable_update(self):
        class Item:
            view_count = 1234
            download_count = 56
            updated_at = timezone.now() - timedelta(days=3)

        html = self.render(Item())

        self.assertIn('title="1234 views"', html)
        self.assertIn("1.2k", html)
        self.assertIn('title="56 downloads"', html)
        self.assertIn("<time datetime=", html)
        self.assertIn("Updated", html)

    def test_prefers_the_meaningful_update_time(self):
        class Item:
            views_count = 3
            updated_at = timezone.now()
            content_updated_at = timezone.now() - timedelta(days=40)

        self.assertIn("1\xa0month ago", self.render(Item()))
