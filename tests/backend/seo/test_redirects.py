from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.seo.models import Redirect
from apps.seo.redirects import (
    RedirectLoop,
    flattened_target,
    follow,
    is_safe_target,
    validate_redirect,
)


class RedirectSafetyTests(TestCase):
    def test_active_redirect_serves_instead_of_404(self):
        Redirect.objects.create(source="/old/", target="/new/", is_permanent=True)

        response = self.client.get("/old/")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/new/")

    def test_temporary_inactive_and_non_get_behaviour(self):
        Redirect.objects.create(source="/temp/", target="/new/", is_permanent=False)
        Redirect.objects.create(source="/off/", target="/new/", is_active=False)

        self.assertEqual(self.client.get("/temp/").status_code, 302)
        self.assertEqual(self.client.get("/off/").status_code, 404)
        self.assertEqual(self.client.post("/temp/").status_code, 404)

    def test_a_live_page_always_wins_over_a_redirect_on_its_path(self):
        Redirect.objects.create(source="/health/live/", target="/elsewhere/")

        self.assertEqual(self.client.get("/health/live/").status_code, 200)

    def test_unsafe_targets_are_rejected_at_the_model_boundary(self):
        for source, target in (
            ("/a/", "javascript:alert(1)"),
            ("/a/", "//evil.invalid/x"),
            ("/a/", "/\\evil.invalid"),
            ("/a/", "/ok/\r\nSet-Cookie: x=1"),
            ("/a/", "data:text/html,x"),
            ("a/", "/b/"),
            ("//a/", "/b/"),
            ("/a/", "/a/"),
        ):
            with (
                self.subTest(source=source, target=target),
                self.assertRaises(ValidationError),
            ):
                Redirect(source=source, target=target).save()

        Redirect(source="/ok/", target="https://example.org/x").full_clean()

    def test_middleware_refuses_an_unsafe_row_written_around_validation(self):
        Redirect.objects.bulk_create(
            [
                Redirect(source="/bulk-evil/", target="//evil.invalid/"),
                Redirect(source="/bulk-backslash/", target="/\\evil.invalid"),
                Redirect(source="/bulk-self/", target="/bulk-self/"),
            ]
        )

        for path in ("/bulk-evil/", "/bulk-backslash/", "/bulk-self/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_direct_and_indirect_loops_are_rejected(self):
        Redirect.objects.create(source="/a/", target="/b/")
        Redirect.objects.create(source="/b/", target="/c/")

        with self.assertRaises(ValidationError):
            Redirect.objects.create(source="/c/", target="/a/")
        with self.assertRaises(ValidationError):
            Redirect.objects.create(source="/c/", target="/b/")

        self.assertEqual(self.client.get("/a/").status_code, 301)

    def test_editing_an_existing_redirect_into_a_loop_is_rejected(self):
        Redirect.objects.create(source="/a/", target="/b/")
        second = Redirect.objects.create(source="/b/", target="/c/")

        second.target = "/a/"
        with self.assertRaises(ValidationError):
            second.save()

    def test_loop_detection_over_a_written_graph_is_bounded(self):
        Redirect.objects.bulk_create(
            [Redirect(source="/x/", target="/y/"), Redirect(source="/y/", target="/x/")]
        )

        with self.assertRaises(RedirectLoop):
            follow("/x/")
        self.assertIsNone(flattened_target("/x/"))
        self.assertEqual(self.client.get("/x/").status_code, 301)

    def test_chain_flattening_returns_the_final_destination(self):
        Redirect.objects.create(source="/one/", target="/two/")
        Redirect.objects.create(source="/two/", target="/three/")

        self.assertEqual(flattened_target("/one/"), "/three/")
        self.assertIsNone(flattened_target("/two/"))

    def test_validation_reports_every_reason(self):
        self.assertEqual(
            validate_redirect("/a/", "/a/"), ["Target must differ from the source."]
        )
        self.assertEqual(validate_redirect("/ok-source/", "https://example.org/x"), [])
        self.assertEqual(len(validate_redirect("bad", "//evil.invalid")), 2)

    def test_safe_target_rules(self):
        for target in ("/ok/", "https://example.org/x", "http://example.org/"):
            self.assertTrue(is_safe_target(target))
        for target in ("", "//x", "/\\x", "ftp://x", "x", "javascript:1", "/a\nb"):
            self.assertFalse(is_safe_target(target))
