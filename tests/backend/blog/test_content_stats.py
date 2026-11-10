"""Article pages: total views (human, once per session per day) and the share component."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.blog.models import Post, PostStatus

SITE = "https://example.org"
BROWSER = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"}


@override_settings(SITE_URL=SITE)
class ArticleTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="stats-author", email="stats-author@example.test"
        )
        self.staff = user_model.objects.create_superuser(
            username="stats-staff", email="stats-staff@example.test"
        )
        self.post = self.make_post("Counted article")
        self.url = self.post.get_absolute_url()

    def make_post(self, title: str, **extra) -> Post:
        values = {
            "title": title,
            "body": f"<p>Body of {title} with enough words to be valid.</p>",
            "author": self.author,
            "status": PostStatus.PUBLISHED,
            "publish_at": timezone.now() - timedelta(hours=1),
            **extra,
        }
        return Post.objects.create(**values)

    def views(self, post: Post | None = None) -> int:
        target = post or self.post
        target.refresh_from_db()
        return target.views_count


class ArticleStatsTests(ArticleTestCase):
    def test_each_human_session_counts_once(self):
        first, second = Client(), Client()

        for client in (first, first, second, first, second):
            client.get(self.url, **BROWSER)

        self.assertEqual(self.views(), 2)

    def test_crawlers_previews_and_header_less_clients_are_not_counted(self):
        for headers in (
            {"HTTP_USER_AGENT": "Googlebot/2.1"},
            {"HTTP_USER_AGENT": "facebookexternalhit/1.1"},
            {"HTTP_USER_AGENT": "WhatsApp/2.23"},
            {"HTTP_USER_AGENT": "Mozilla/5.0 Firefox/130", "HTTP_SEC_PURPOSE": "prefetch"},
            {},
        ):
            Client().get(self.url, **headers)

        self.assertEqual(self.views(), 0)

    def test_staff_and_author_previews_of_unpublished_posts_are_not_counted(self):
        draft = self.make_post("Draft preview", status=PostStatus.DRAFT, publish_at=None)
        scheduled = self.make_post(
            "Scheduled preview",
            status=PostStatus.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=2),
        )
        for user in (self.staff, self.author):
            client = Client()
            client.force_login(user)
            for post in (draft, scheduled):
                response = client.get(post.get_absolute_url(), **BROWSER)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Preview.")

        self.assertEqual(self.views(draft), 0)
        self.assertEqual(self.views(scheduled), 0)

    def test_the_increment_is_one_atomic_relative_update_that_keeps_updated_at(self):
        before = Post.objects.get(pk=self.post.pk).updated_at

        with CaptureQueriesContext(connection) as queries:
            Client().get(self.url, **BROWSER)

        updates = [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].startswith('UPDATE "blog_post" SET "views_count"')
        ]
        self.assertEqual(len(updates), 1, updates)
        self.assertIn('"views_count" = ("blog_post"."views_count" + 1)', updates[0])
        self.assertNotIn("updated_at", updates[0])
        self.post.refresh_from_db()
        self.assertEqual(self.post.updated_at, before)

    def test_the_page_shows_the_exact_total_and_the_update_line(self):
        Post.objects.filter(pk=self.post.pk).update(views_count=1999)

        html = Client().get(self.url, **BROWSER).content.decode()

        self.assertIn('title="2000 views"', html)
        self.assertIn("2k", html)
        self.assertIn("Updated", html)
        self.assertIn("min read", html)

    def test_a_single_view_reads_as_singular(self):
        html = Client().get(self.url, **BROWSER).content.decode()

        self.assertIn('title="1 view"', html)
        self.assertNotIn("1 views", html)

    def test_popular_list_shows_views(self):
        Post.objects.filter(pk=self.post.pk).update(views_count=4321)

        html = Client().get("/blog/").content.decode()

        self.assertIn("4.3k views", html)


class ArticleShareTests(ArticleTestCase):
    def test_share_component_uses_the_canonical_absolute_url(self):
        html = Client().get(self.url, **BROWSER).content.decode()
        canonical = f"{SITE}{self.url}"

        self.assertIn(f'data-share-url="{canonical}"', html)
        self.assertIn(f'data-copy="{canonical}"', html)
        self.assertIn('data-copy-message="Article link copied."', html)
        self.assertIn("Share this article", html)
        self.assertIn("x.com/intent/post?url=https%3A%2F%2Fexample.org%2Fblog%2F", html)
        self.assertEqual(html.count('rel="noopener noreferrer"'), 5)

    def test_an_explicit_canonical_url_wins_over_the_page_path(self):
        post = self.make_post(
            "Syndicated", canonical_url="https://origin.example/articles/syndicated/"
        )

        html = Client().get(post.get_absolute_url(), **BROWSER).content.decode()

        self.assertIn('data-share-url="https://origin.example/articles/syndicated/"', html)

    def test_previews_are_not_shareable(self):
        draft = self.make_post("Draft", status=PostStatus.DRAFT, publish_at=None)
        client = Client()
        client.force_login(self.staff)

        html = client.get(draft.get_absolute_url(), **BROWSER).content.decode()

        self.assertNotIn("data-share", html)
