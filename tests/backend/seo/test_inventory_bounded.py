"""The page inventory resolves a bounded slice per request.

Regression for the crawl timeout at ``/seo/inventory/`` (>30 s on the seeded DB): the
view called ``build_inventory()``, which runs the full SEO resolver (several queries)
for every static route and every sitemap URL, and rendered all of them in one table.
"""

from __future__ import annotations

from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.seo import inventory
from apps.seo.models import Issue
from apps.seo.views import ROUTE_PAGE_SIZE
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_staff


def _entries(count: int) -> list[tuple[str, str]]:
    return [(f"/help/inv-{index:05d}/", "sitemap:pages") for index in range(count)]


@override_settings(SITE_URL=SITE)
class InventoryIsBoundedTests(TestCase):
    def setUp(self):
        make_catalog()
        self.client.force_login(make_staff("inventory_staff", superuser=True))

    def _get(self, entries: list[tuple[str, str]], **params):
        with (
            mock.patch("apps.seo.views.discover_entries", return_value=entries),
            mock.patch(
                "apps.seo.views.describe_entry", wraps=inventory.describe_entry
            ) as describe,
            CaptureQueriesContext(connection) as queries,
        ):
            response = self.client.get(reverse("seo:inventory"), params)
        return response, describe, len(queries)

    def test_only_one_page_of_routes_is_resolved(self):
        response, describe, _queries = self._get(_entries(3000))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(describe.call_count, ROUTE_PAGE_SIZE)
        self.assertEqual(len(response.context["items"]), ROUTE_PAGE_SIZE)
        self.assertEqual(response.context["discovered_total"], 3000)

    def test_query_count_does_not_grow_with_the_number_of_discovered_urls(self):
        self._get(_entries(ROUTE_PAGE_SIZE))  # warm per-process caches
        _small, _calls, small = self._get(_entries(ROUTE_PAGE_SIZE))
        _large, _calls, large = self._get(_entries(5000))

        self.assertEqual(small, large)

    def test_later_route_pages_are_reachable_and_bounded(self):
        response, describe, _queries = self._get(_entries(120), route_page=3)

        paths = [item.path for item in response.context["items"]]
        self.assertEqual(
            paths, [path for path, _source in _entries(120)][2 * ROUTE_PAGE_SIZE :]
        )
        self.assertEqual(describe.call_count, 120 - 2 * ROUTE_PAGE_SIZE)
        self.assertContains(response, "route_page=2")

    def test_an_out_of_range_route_page_is_clamped_not_an_error(self):
        response, describe, _queries = self._get(_entries(60), route_page=999)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(describe.call_count, 60 - ROUTE_PAGE_SIZE)

    def test_totals_come_from_stored_scan_results(self):
        Issue.objects.create(
            code="inventory_conflict",
            severity="error",
            url="/firmwares/nova/",
            message="Sitemap URL conflicts with policy: noindex_in_sitemap.",
            fingerprint="inventory-bounded-1",
        )

        response, _describe, _queries = self._get(_entries(3))

        self.assertEqual(response.context["conflict_total"], 1)
        self.assertContains(response, "3 discovered URLs")

    def test_real_discovery_still_lists_catalogue_routes(self):
        response = self.client.get(reverse("seo:inventory"), {"route_page": 1})

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.context["items"]), ROUTE_PAGE_SIZE)
        self.assertGreater(response.context["discovered_total"], 0)

    def test_the_search_box_narrows_the_routes_before_they_are_resolved(self):
        response, describe, _queries = self._get(
            [*_entries(500), ("/help/needle/", "sitemap:pages")], q="needle"
        )

        self.assertEqual(describe.call_count, 1)
        self.assertEqual(
            [item.path for item in response.context["items"]], ["/help/needle/"]
        )
        self.assertEqual(response.context["discovered_total"], 501)
