from __future__ import annotations

from django.test import TestCase
from django.urls import get_resolver, reverse

from tests.url_inventory import named_routes


class NamedUrlSmokeTests(TestCase):
    def test_homepage_and_changelog_api_work_on_a_fresh_schema(self):
        homepage = self.client.get(reverse("home"))
        changelog = self.client.get(reverse("firmwares:changelog-entries-list"))

        self.assertEqual(homepage.status_code, 200)
        self.assertEqual(changelog.status_code, 200)
        self.assertEqual(changelog.json(), [])

    def test_staff_pages_redirect_to_the_registered_admin_login(self):
        routes = (
            "comments:moderation_queue",
            "comments:moderation_action",
            "tags:duplicates_review",
            "tags:keyword_review",
            "tags:apply_keyword",
            "devices:device_events",
        )

        for route_name in routes:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response["Location"].startswith("/admin/login/?next="),
                    response["Location"],
                )

    def test_every_named_get_route_responds_without_a_server_error(self):
        routes = list(named_routes(get_resolver().url_patterns))
        self.assertTrue(routes, "No named URL routes were discovered.")

        failures = []
        for route_name, kwargs in routes:
            path = reverse(route_name, kwargs=kwargs or None)
            response = self.client.get(path)
            if response.status_code >= 500:
                failures.append(
                    f"{route_name} ({path}) returned {response.status_code}"
                )

        self.assertEqual(failures, [], "\n".join(failures))
