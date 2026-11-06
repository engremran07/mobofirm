from __future__ import annotations

from typing import ClassVar
from urllib.parse import urljoin, urlsplit

from django.test import Client, TestCase, override_settings

from apps.seo import audit
from apps.seo.crawler import (
    Crawler,
    CrawlUnavailable,
    normalize,
    parse_html,
    parse_sitemap,
    run_crawl,
)
from apps.seo.fetcher import FetchResult, SafeFetcher
from apps.seo.inventory import resolve_path
from apps.seo.models import CrawlRun, Issue, PageObservation, SEOSettings
from tests.backend.seo.seo_helpers import SITE, Catalog, make_catalog


class ClientFetcher(SafeFetcher):
    """Serves the real Django app through the crawler without any socket."""

    def __init__(self, client: Client | None = None, overrides=None):
        super().__init__()
        self.client = client or Client()
        self.overrides = overrides or {}
        self.requested: list[str] = []

    def fetch(self, url, **kwargs):
        chain = []
        current = url
        for _ in range(6):
            self.requested.append(current)
            parts = urlsplit(current)
            if current in self.overrides:
                response = self.overrides[current]
            else:
                path = parts.path + (f"?{parts.query}" if parts.query else "")
                response = self.client.get(path, HTTP_HOST=parts.netloc)
            location = response.headers.get("Location", "")
            if response.status_code in {301, 302} and location:
                target = urljoin(current, location)
                chain.append((response.status_code, current, target))
                current = target
                continue
            return FetchResult(
                url=url,
                final_url=current,
                status=response.status_code,
                headers={k.lower(): v for k, v in response.headers.items()},
                body=response.content,
                redirects=tuple(chain),
                elapsed_ms=5,
            )
        raise AssertionError("redirect loop in test fetcher")


def page(html: str, status=200, headers=None):
    from django.http import HttpResponse

    response = HttpResponse(html, status=status, content_type="text/html")
    for key, value in (headers or {}).items():
        response[key] = value
    return response


class ParserTests(TestCase):
    def test_parser_extracts_the_raw_head_and_links(self):
        parsed = parse_html(
            """<html lang="en"><head><title> A  title </title>
            <meta name="description" content="Desc"><meta name="robots" content="noindex">
            <link rel="canonical" href="https://example.org/a/">
            <link rel="alternate" hreflang="de" href="https://example.org/de/a/">
            <script type="application/ld+json">{"@type":"Article"}</script>
            <script type="application/ld+json">{not json}</script></head>
            <body><h1>Head</h1><a href="/x/">x</a><img src="a.png"><img src="b.png" alt=""></body></html>"""
        )

        self.assertEqual(parsed.titles, ["A title"])
        self.assertEqual(parsed.descriptions, ["Desc"])
        self.assertEqual(parsed.robots_meta, ["noindex"])
        self.assertEqual(parsed.canonicals, ["https://example.org/a/"])
        self.assertEqual(
            parsed.hreflang, [{"lang": "de", "href": "https://example.org/de/a/"}]
        )
        self.assertEqual(parsed.h1s, ["Head"])
        self.assertEqual(parsed.links, ["/x/"])
        self.assertEqual(parsed.jsonld_types, ["Article"])
        self.assertEqual(parsed.jsonld_errors, 1)
        self.assertEqual(parsed.images_missing_alt, 1)
        self.assertEqual(parsed.lang, "en")

    def test_normalize_keeps_only_same_site_html_urls(self):
        base = "https://example.org/a/"
        origin = "https://example.org"

        self.assertEqual(
            normalize("/b/?x=1#frag", base, origin), "https://example.org/b/?x=1"
        )
        for href in (
            "https://other.example/",
            "mailto:a@b.c",
            "javascript:void(0)",
            "/f.zip",
            "/i.png",
            "",
        ):
            self.assertIsNone(normalize(href, base, origin), href)

    def test_sitemap_parsing_handles_indexes_and_garbage(self):
        children, pages = parse_sitemap(
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://example.org/s1.xml</loc></sitemap></sitemapindex>"
        )
        self.assertEqual((children, pages), (["https://example.org/s1.xml"], []))
        self.assertEqual(parse_sitemap("<<not xml"), ([], []))


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=["example.org", "testserver"])
class CrawlerEndToEndTests(TestCase):
    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()
        row = SEOSettings.get_solo()
        row.autonomy_mode = SEOSettings.AutonomyMode.SUGGEST
        row.crawl_delay_ms = 0
        row.save()

    def test_full_site_crawl_matches_the_resolver_exactly(self):
        fetcher = ClientFetcher()

        run = run_crawl(fetcher=fetcher, sleep=lambda _s: None)

        self.assertEqual(run.status, CrawlRun.Status.COMPLETED)
        self.assertGreater(run.pages_crawled, 10)
        crawled = {obs.url for obs in run.observations.all()}
        for path in (
            "/",
            "/firmwares/nova/",
            "/firmwares/nova/atlas-one/",
            "/blog/how-to-flash-the-atlas-one/",
        ):
            self.assertIn(f"{SITE}{path}", crawled)
        structural = {
            "head_mismatch",
            "multiple_titles",
            "multiple_canonicals",
            "multiple_robots_meta",
            "missing_title",
            "canonical_not_absolute",
            "invalid_json_ld",
            "noindex_in_sitemap",
            "sitemap_canonical_mismatch",
            "sitemap_url_error",
            "inventory_conflict",
        }
        found = Issue.objects.filter(code__in=structural, status=Issue.Status.OPEN)
        self.assertEqual(
            list(found.values_list("code", "url", "message")),
            [],
        )
        self.assertIsNotNone(run.health_percent)
        self.assertTrue(
            PageObservation.objects.filter(run=run, in_sitemap=True).exists()
        )

    def test_crawl_never_leaves_the_site_and_respects_the_page_budget(self):
        row = SEOSettings.get_solo()
        row.crawl_max_pages = 3
        row.save()
        fetcher = ClientFetcher()

        run = run_crawl(fetcher=fetcher, sleep=lambda _s: None)

        self.assertEqual(run.pages_crawled, 3)
        self.assertTrue(all(url.startswith(SITE) for url in fetcher.requested))

    def test_robots_disallow_is_respected(self):
        fetcher = ClientFetcher()
        run = CrawlRun.objects.create(base_url=SITE)

        Crawler(SITE, max_pages=60, fetcher=fetcher).run(run)

        blocked = PageObservation.objects.filter(run=run, blocked_by_robots=True)
        self.assertFalse(
            [
                url
                for url in fetcher.requested
                if "/admin/" in url or "/accounts/" in url
            ],
            "robots-disallowed paths must not be fetched",
        )
        self.assertTrue(all(obs.status_code is None for obs in blocked))

    def test_blocked_destinations_fail_visibly_without_a_connection(self):
        run = run_crawl(base_url="http://127.0.0.1:9/", sleep=lambda _s: None)

        self.assertEqual(run.status, CrawlRun.Status.COMPLETED)
        self.assertTrue(
            Issue.objects.filter(
                code="fetch_failed", message__contains="blocked_destination"
            ).exists()
        )

    def test_crawl_is_unavailable_when_automation_is_off_or_already_running(self):
        row = SEOSettings.get_solo()
        row.autonomy_mode = SEOSettings.AutonomyMode.OFF
        row.save()
        with self.assertRaises(CrawlUnavailable):
            run_crawl(fetcher=ClientFetcher())

        row.autonomy_mode = SEOSettings.AutonomyMode.SUGGEST
        row.save()
        CrawlRun.objects.create(base_url=SITE, status=CrawlRun.Status.RUNNING)
        with self.assertRaises(CrawlUnavailable):
            run_crawl(fetcher=ClientFetcher())

    def test_an_unexpected_failure_marks_the_run_failed_and_propagates(self):
        class Exploding(ClientFetcher):
            def fetch(self, url, **kwargs):
                raise OSError("disk on fire")

        with self.assertRaises(OSError):
            run_crawl(fetcher=Exploding())

        run = CrawlRun.objects.latest("created_at")
        self.assertEqual(run.status, CrawlRun.Status.FAILED)
        self.assertIn("disk on fire", run.error)

    def test_crawl_requires_a_configured_site(self):
        with override_settings(SITE_URL=""), self.assertRaises(CrawlUnavailable):
            run_crawl(fetcher=ClientFetcher())


def observation(url, **fields):
    defaults = {
        "run": None,
        "status_code": 200,
        "content_type": "text/html",
        "titles": ["Title"],
        "descriptions": ["Description"],
        "canonicals": [url],
        "h1s": ["Heading"],
        "robots_meta": ["index, follow"],
    }
    defaults.update(fields)
    return PageObservation(url=url, **defaults)


class AuditCheckTests(TestCase):
    def codes(self, observations):
        return {(f.code, f.url) for f in audit.analyze_observations(observations)}

    def test_duplicate_missing_and_multiple_head_elements(self):
        found = self.codes(
            [
                observation("https://example.org/", depth=0),
                observation(
                    "https://example.org/a/", titles=["Same"], descriptions=["Same d"]
                ),
                observation(
                    "https://example.org/b/", titles=["Same"], descriptions=["Same d"]
                ),
                observation(
                    "https://example.org/c/", titles=[], descriptions=[], canonicals=[]
                ),
                observation(
                    "https://example.org/d/",
                    titles=["x", "y"],
                    canonicals=["/rel/", "/rel2/"],
                ),
            ]
        )

        self.assertIn(("duplicate_title", "https://example.org/a/"), found)
        self.assertIn(("duplicate_description", "https://example.org/b/"), found)
        self.assertIn(("missing_title", "https://example.org/c/"), found)
        self.assertIn(("missing_description", "https://example.org/c/"), found)
        self.assertIn(("missing_canonical", "https://example.org/c/"), found)
        self.assertIn(("multiple_titles", "https://example.org/d/"), found)
        self.assertIn(("multiple_canonicals", "https://example.org/d/"), found)

    def test_canonical_sitemap_and_noindex_conflicts(self):
        found = self.codes(
            [
                observation("https://example.org/", depth=0),
                observation(
                    "https://example.org/a/",
                    in_sitemap=True,
                    canonicals=["https://example.org/other/"],
                ),
                observation(
                    "https://example.org/b/", in_sitemap=True, robots_meta=["noindex"]
                ),
                observation(
                    "https://example.org/c/", in_sitemap=True, x_robots_tag="noindex"
                ),
                observation(
                    "https://example.org/d/", canonicals=["https://example.org/gone/"]
                ),
                observation("https://example.org/gone/", status_code=404),
                observation("https://example.org/e/", canonicals=["relative/"]),
                observation(
                    "https://example.org/f/", in_sitemap=True, blocked_by_robots=True
                ),
            ]
        )

        self.assertIn(("sitemap_canonical_mismatch", "https://example.org/a/"), found)
        self.assertIn(("noindex_in_sitemap", "https://example.org/b/"), found)
        self.assertIn(("noindex_in_sitemap", "https://example.org/c/"), found)
        self.assertIn(("canonical_to_error", "https://example.org/d/"), found)
        self.assertIn(("canonical_not_absolute", "https://example.org/e/"), found)
        self.assertIn(("robots_blocked_in_sitemap", "https://example.org/f/"), found)

    def test_links_redirects_and_orphans(self):
        chain = [
            {
                "status": 301,
                "from": "https://example.org/old/",
                "to": "https://example.org/mid/",
            },
            {
                "status": 301,
                "from": "https://example.org/mid/",
                "to": "https://example.org/new/",
            },
        ]
        found = self.codes(
            [
                observation(
                    "https://example.org/",
                    depth=0,
                    internal_links=[
                        "https://example.org/dead/",
                        "https://example.org/old/",
                        "https://example.org/new/",
                    ],
                ),
                observation("https://example.org/dead/", status_code=404),
                observation(
                    "https://example.org/old/",
                    redirect_chain=chain,
                    final_url="https://example.org/new/",
                ),
                observation("https://example.org/new/"),
                observation("https://example.org/orphan/"),
            ]
        )

        self.assertIn(("broken_internal_link", "https://example.org/"), found)
        self.assertIn(("redirect_internal_link", "https://example.org/"), found)
        self.assertIn(("redirect_chain", "https://example.org/old/"), found)
        self.assertIn(("orphan_page", "https://example.org/orphan/"), found)
        self.assertNotIn(("orphan_page", "https://example.org/new/"), found)
        self.assertNotIn(("orphan_page", "https://example.org/"), found)

    def test_hreflang_reciprocity_codes_and_self_reference(self):
        en, de = "https://example.org/en/", "https://example.org/de/"
        found = self.codes(
            [
                observation(
                    en,
                    depth=0,
                    hreflang=[{"lang": "en", "href": en}, {"lang": "de", "href": de}],
                    internal_links=[de],
                ),
                observation(
                    de, hreflang=[{"lang": "de", "href": de}], internal_links=[en]
                ),
                observation(
                    "https://example.org/x/",
                    hreflang=[{"lang": "english", "href": "https://example.org/y/"}],
                ),
            ]
        )

        self.assertIn(("hreflang_missing_return", en), found)
        self.assertNotIn(("hreflang_missing_return", de), found)
        self.assertIn(("hreflang_invalid_code", "https://example.org/x/"), found)
        self.assertIn(("hreflang_missing_self", "https://example.org/x/"), found)

    def test_json_ld_images_and_slow_pages_are_info_or_errors(self):
        found = self.codes(
            [
                observation(
                    "https://example.org/",
                    depth=0,
                    internal_links=["https://example.org/p/"],
                ),
                observation(
                    "https://example.org/p/",
                    jsonld_errors=2,
                    images_missing_alt=3,
                    response_ms=9000,
                    h1s=[],
                ),
            ]
        )

        self.assertIn(("invalid_json_ld", "https://example.org/p/"), found)
        self.assertIn(("images_missing_alt", "https://example.org/p/"), found)
        self.assertIn(("slow_response", "https://example.org/p/"), found)
        self.assertIn(("missing_h1", "https://example.org/p/"), found)

    def test_noindex_pages_do_not_raise_indexation_warnings(self):
        found = self.codes(
            [
                observation("https://example.org/", depth=0),
                observation(
                    "https://example.org/n/",
                    robots_meta=["noindex, follow"],
                    descriptions=[],
                    canonicals=[],
                    h1s=[],
                ),
            ]
        )

        self.assertFalse(
            {code for code, url in found if url.endswith("/n/")} - {"orphan_page"}
        )

    def test_head_mismatch_compares_served_html_with_the_resolver(self):
        make_catalog()
        with override_settings(SITE_URL=SITE):
            found = self.codes(
                [
                    observation(
                        f"{SITE}/firmwares/nova/",
                        titles=["Wrong title"],
                        canonicals=[f"{SITE}/firmwares/nova/"],
                        robots_meta=["noindex"],
                    )
                ]
            )

        self.assertIn(("head_mismatch", f"{SITE}/firmwares/nova/"), found)

    def test_head_of_a_redirected_page_is_compared_with_the_final_url(self):
        make_catalog()
        with override_settings(SITE_URL=SITE):
            expected = resolve_path("/firmwares/nova/")
            assert expected is not None
            served = observation(
                f"{SITE}/blog/old-link/",
                final_url=f"{SITE}/firmwares/nova/",
                redirect_chain=[
                    {
                        "status": 302,
                        "from": f"{SITE}/blog/old-link/",
                        "to": f"{SITE}/firmwares/nova/",
                    }
                ],
                titles=[expected.title],
                canonicals=[expected.canonical],
                robots_meta=[] if expected.indexable else ["noindex"],
            )
            found = self.codes([served])

        self.assertNotIn(("head_mismatch", f"{SITE}/blog/old-link/"), found)


class IssueLifecycleTests(TestCase):
    def test_issues_upsert_by_fingerprint_and_resolve_when_fixed(self):
        run = CrawlRun.objects.create(base_url=SITE)
        finding = audit.Finding(
            "missing_title", Issue.Severity.ERROR, f"{SITE}/a/", "No title"
        )

        audit.sync_issues(run, [finding], {f"{SITE}/a/"})
        audit.sync_issues(run, [finding], {f"{SITE}/a/"})
        self.assertEqual(Issue.objects.count(), 1)

        audit.sync_issues(run, [], {f"{SITE}/a/"})
        self.assertEqual(Issue.objects.get().status, Issue.Status.RESOLVED)

        audit.sync_issues(run, [finding], {f"{SITE}/a/"})
        self.assertEqual(Issue.objects.get().status, Issue.Status.OPEN)

    def test_unobserved_urls_are_not_resolved_by_a_partial_crawl(self):
        run = CrawlRun.objects.create(base_url=SITE)
        finding = audit.Finding(
            "missing_title", Issue.Severity.ERROR, f"{SITE}/a/", "No title"
        )
        audit.sync_issues(run, [finding], {f"{SITE}/a/"})

        audit.sync_issues(run, [], {f"{SITE}/other/"})

        self.assertEqual(Issue.objects.get().status, Issue.Status.OPEN)

    def test_health_is_the_share_of_error_free_pages_and_nothing_else(self):
        run = CrawlRun(pages_crawled=8, pages_with_errors=2)
        self.assertEqual(run.health_percent, 75)
        self.assertIsNone(CrawlRun(pages_crawled=0).health_percent)
        self.assertFalse(hasattr(run, "score"))
        self.assertFalse(hasattr(run, "serp_score"))

    @override_settings(SITE_URL=SITE)
    def test_inventory_audit_flags_a_sitemap_policy_conflict(self):
        make_catalog()
        findings = audit.analyze_inventory()
        self.assertEqual([f for f in findings if f.code == "inventory_conflict"], [])

        with override_settings(SITE_URL=""):
            self.assertIn(
                "site_url_missing", {f.code for f in audit.analyze_inventory()}
            )
