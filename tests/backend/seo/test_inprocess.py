from __future__ import annotations

from django.test import TestCase, override_settings

from apps.seo import scan
from apps.seo.crawler import run_crawl
from apps.seo.fetcher import BlockedDestination, FetchError, SafeFetcher
from apps.seo.inprocess import InProcessFetcher, choose_fetcher, self_crawl
from apps.seo.models import CrawlRun, PageObservation, PageSnapshot, Redirect
from tests.backend.seo.seo_helpers import SITE, make_catalog

HOSTS = ["example.org", "testserver", "localhost"]


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class InProcessFetcherTests(TestCase):
    def test_serves_pages_through_the_real_url_conf(self):
        make_catalog()

        result = InProcessFetcher().fetch(
            f"{SITE}/firmwares/nova/atlas-one/",
            allowed_hosts=frozenset({"example.org"}),
        )

        self.assertEqual(result.status, 200)
        self.assertEqual(result.content_type, "text/html")
        self.assertIn("Atlas One", result.text)
        self.assertEqual(result.redirects, ())

    def test_follows_redirects_and_records_the_chain(self):
        Redirect.objects.create(
            source="/old-atlas/", target="/firmwares/nova/atlas-one/"
        )
        make_catalog()

        result = InProcessFetcher().fetch(f"{SITE}/old-atlas/")

        self.assertEqual(result.status, 200)
        self.assertEqual(result.final_url, f"{SITE}/firmwares/nova/atlas-one/")
        self.assertEqual(result.redirects[0][0], 301)

    def test_other_hosts_and_methods_are_refused(self):
        with self.assertRaises(BlockedDestination):
            InProcessFetcher().fetch(
                "https://evil.example/", allowed_hosts=frozenset({"example.org"})
            )
        with self.assertRaises(FetchError):
            InProcessFetcher().fetch(f"{SITE}/", method="DELETE")

    def test_missing_pages_return_their_status(self):
        self.assertEqual(InProcessFetcher().fetch(f"{SITE}/no/such/page/").status, 404)


class TransportChoiceTests(TestCase):
    @override_settings(ALLOWED_HOSTS=HOSTS)
    def test_the_site_crawls_itself_in_process_and_others_over_http(self):
        self.assertTrue(self_crawl(SITE))
        self.assertFalse(self_crawl("https://elsewhere.example"))
        self.assertIsInstance(choose_fetcher(SITE), InProcessFetcher)
        other = choose_fetcher("https://elsewhere.example")
        self.assertIsInstance(other, SafeFetcher)
        self.assertNotIsInstance(other, InProcessFetcher)

    @override_settings(ALLOWED_HOSTS=HOSTS, SEO_CRAWL_TRANSPORT="http")
    def test_the_transport_can_be_forced_to_http(self):
        self.assertFalse(self_crawl(SITE))

    @override_settings(ALLOWED_HOSTS=["other"], SEO_CRAWL_TRANSPORT="inprocess")
    def test_the_transport_can_be_forced_in_process(self):
        self.assertTrue(self_crawl(SITE))


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class SelfCrawlTests(TestCase):
    def test_a_render_crawl_completes_without_a_network_and_stores_link_counts(self):
        make_catalog()
        scan.run_scan()

        run = run_crawl(sleep=lambda _seconds: None)

        self.assertEqual(run.status, CrawlRun.Status.COMPLETED)
        self.assertGreater(run.pages_crawled, 5)
        observed = PageObservation.objects.filter(run=run)
        self.assertTrue(
            observed.filter(url__endswith="/firmwares/nova/atlas-one/").exists()
        )
        self.assertGreater(observed.filter(status_code=200).count(), 3)
        self.assertTrue(
            PageSnapshot.objects.filter(path="/firmwares/nova/atlas-one/").exists()
        )
        self.assertTrue(PageSnapshot.objects.filter(links_in__gt=0).exists())
        self.assertTrue(PageSnapshot.objects.filter(links_out__gt=0).exists())
