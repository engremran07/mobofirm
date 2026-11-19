from __future__ import annotations

import dataclasses
from typing import ClassVar
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.ads.services import zones
from apps.core.internal_request import (
    ENVIRON_KEY,
    internal_crawl_environ,
    is_internal_crawl,
)
from apps.core.view_counter import is_bot_request, should_count
from apps.referral import services as referral_services
from apps.seo import scan
from apps.seo.crawler import run_crawl
from apps.seo.fetcher import BlockedDestination, FetchPolicy
from apps.seo.inprocess import InProcessFetcher
from apps.seo.models import CrawlRun
from tests.backend.seo.seo_helpers import SITE, Catalog, make_catalog

HOSTS = ["example.org", "testserver", "localhost"]
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"


def browser_fetcher() -> InProcessFetcher:
    """An in-process fetcher whose user agent looks like a person, not a bot."""
    policy = dataclasses.replace(FetchPolicy.from_settings(), user_agent=BROWSER)
    return InProcessFetcher(policy=policy)


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class CrawlerIdentityTests(TestCase):
    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def counters(self) -> dict[str, int]:
        self.data.post.refresh_from_db()
        self.data.tool.refresh_from_db()
        self.data.firmware.refresh_from_db()
        return {
            "post": self.data.post.views_count,
            "tool": self.data.tool.views_count,
            "firmware": self.data.firmware.view_count,
        }

    def paths(self) -> list[str]:
        return [
            self.data.post.get_absolute_url(),
            reverse("firmwares:flashing_tool_detail", args=["flasher"]),
            reverse(
                "firmwares:firmware_detail", args=["official", self.data.firmware.pk]
            ),
        ]

    def test_a_real_visitor_is_counted_so_the_control_is_meaningful(self):
        before = self.counters()

        for path in self.paths():
            self.client.get(path, headers={"User-Agent": BROWSER})

        after = self.counters()
        self.assertEqual(
            {key: after[key] - before[key] for key in after}, dict.fromkeys(after, 1)
        )

    def test_in_process_fetches_render_pages_but_never_count_even_with_a_browser_agent(
        self,
    ):
        before = self.counters()
        fetcher = browser_fetcher()

        for path in self.paths():
            result = fetcher.fetch(f"{SITE}{path}")
            self.assertEqual(result.status, 200)
            self.assertIn("<title>", result.text)

        self.assertEqual(self.counters(), before)

    def test_a_full_render_crawl_leaves_counters_and_analytics_unchanged(self):
        scan.run_scan()
        before = self.counters()

        run = run_crawl(fetcher=browser_fetcher(), sleep=lambda _seconds: None)

        self.assertEqual(run.status, CrawlRun.Status.COMPLETED)
        self.assertGreater(run.pages_crawled, 5)
        self.assertEqual(self.counters(), before)

    def test_the_crawler_never_requests_private_download_or_token_urls(self):
        fetcher = browser_fetcher()

        for path in (
            "/admin/",
            "/accounts/logout/",
            "/shop/orders/1/",
            "/pricing/orders/1/",
            "/firmwares/serve/abc/",
            "/firmwares/file/official/1/download/",
        ):
            with self.subTest(path=path), self.assertRaises(BlockedDestination):
                fetcher.fetch(f"{SITE}{path}")

    def test_mutating_methods_are_refused(self):
        from apps.seo.fetcher import FetchError

        with self.assertRaises(FetchError):
            browser_fetcher().fetch(f"{SITE}/", method="POST")


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class ExternalRequestsAreNotTrustedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def views(self) -> int:
        self.data.post.refresh_from_db()
        return self.data.post.views_count

    def test_the_identity_is_an_environ_entry_not_a_header(self):
        factory = RequestFactory()

        self.assertFalse(is_internal_crawl(factory.get("/")))
        self.assertTrue(is_internal_crawl(factory.get("/", **internal_crawl_environ())))
        self.assertFalse(is_internal_crawl(factory.get("/", **{ENVIRON_KEY: "guess"})))
        self.assertFalse(is_internal_crawl(factory.get("/", **{ENVIRON_KEY: True})))

    def test_a_network_header_carrying_the_real_secret_is_ignored(self):
        secret = internal_crawl_environ()[ENVIRON_KEY]
        before = self.views()

        self.client.get(
            self.data.post.get_absolute_url(),
            headers={
                "User-Agent": BROWSER,
                "X-Internal-Crawl": secret,
                "X-Mobofirm-Internal-Crawl": secret,
            },
        )

        self.assertEqual(self.views(), before + 1)
        request = RequestFactory().get(
            "/", HTTP_X_INTERNAL_CRAWL=secret, HTTP_USER_AGENT=BROWSER
        )
        self.assertFalse(is_internal_crawl(request))
        self.assertFalse(is_bot_request(request))

    def test_a_forged_environ_value_does_not_suppress_counting(self):
        before = self.views()

        self.client.get(
            self.data.post.get_absolute_url(),
            HTTP_USER_AGENT=BROWSER,
            **{ENVIRON_KEY: "forged"},
        )

        self.assertEqual(self.views(), before + 1)

    def test_should_count_is_silent_for_the_internal_identity_only(self):
        factory = RequestFactory()
        internal = factory.get("/", HTTP_USER_AGENT=BROWSER, **internal_crawl_environ())
        visitor = factory.get("/", HTTP_USER_AGENT=BROWSER)
        visitor.session = {}

        self.assertFalse(should_count(internal, "post", 1))
        self.assertTrue(should_count(visitor, "post", 1))

    def test_ad_impressions_and_referral_clicks_are_suppressed_for_the_crawler(self):
        factory = RequestFactory()
        internal = factory.get("/", **internal_crawl_environ())
        external = factory.get("/")
        external.user = mock.Mock(is_authenticated=False)
        creative = mock.Mock()

        with mock.patch.object(zones, "record_event") as record:
            zones._record_impression(internal, mock.Mock(), creative)
            record.assert_not_called()
            zones._record_impression(external, mock.Mock(), creative)
            record.assert_called_once()

        with mock.patch.object(referral_services, "find_code") as find:
            self.assertFalse(referral_services.capture_code(internal, "ABCDEF"))
            find.assert_not_called()
