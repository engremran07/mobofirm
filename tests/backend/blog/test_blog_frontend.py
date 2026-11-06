from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, Category, Post, PostStatus
from apps.tags.models import Tag


class JournalTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="journal-author",
            email="journal-author@example.com",
            full_name="Jamie Writer",
        )
        self.post = self.make_post(
            "A practical firmware guide",
            summary="How to identify the right device firmware.",
            body="<p>A practical explanation with verified technical context.</p>",
        )

    def make_post(self, title, *, body=None, status=PostStatus.PUBLISHED, **extra):
        values = {
            "title": title,
            "body": body or f"<p>Body of {title} with enough words.</p>",
            "author": self.author,
            "status": status,
        }
        if status == PostStatus.PUBLISHED:
            values["publish_at"] = timezone.now() - timedelta(minutes=1)
        values.update(extra)
        return Post.objects.create(**values)


class JournalListingTests(JournalTestCase):
    def test_listing_shows_published_posts_with_the_lead_story_and_search(self):
        response = self.client.get(reverse("blog:post_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertContains(response, "Featured story")
        self.assertContains(response, "Search articles")
        self.assertContains(response, "Jamie Writer")

    def test_author_email_is_never_published(self):
        for url in (
            reverse("blog:post_list"),
            reverse("blog:post_detail", kwargs={"slug": self.post.slug}),
        ):
            self.assertNotContains(self.client.get(url), "journal-author@example.com")

    def test_unpublished_scheduled_and_future_posts_are_hidden(self):
        draft = self.make_post("Hidden draft", status=PostStatus.DRAFT)
        review = self.make_post("Hidden review", status=PostStatus.IN_REVIEW)
        scheduled = self.make_post(
            "Hidden scheduled",
            status=PostStatus.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=1),
        )
        future = self.make_post(
            "Hidden future", publish_at=timezone.now() + timedelta(days=1)
        )

        response = self.client.get(reverse("blog:post_list"))

        for post in (draft, review, scheduled, future):
            self.assertNotContains(response, post.title)
            detail = self.client.get(post.get_absolute_url())
            self.assertEqual(detail.status_code, 404, post.title)

    def test_htmx_request_returns_only_the_results_fragment(self):
        response = self.client.get(reverse("blog:post_list"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertNotContains(response, "<!doctype html>")
        self.assertNotContains(response, "journal-masthead")
        self.assertIn("HX-Request", response.headers["Vary"])

    def test_search_and_category_filters_narrow_results(self):
        category = Category.objects.create(name="Guides")
        match = self.make_post("Odin flashing walkthrough", category=category)
        other = self.make_post("Unrelated release notes")

        by_query = self.client.get(reverse("blog:post_list"), {"q": "odin"})
        by_category = self.client.get(
            reverse("blog:post_list"), {"category": category.slug}
        )
        search_route = self.client.get(reverse("blog:blog_search"), {"q": "odin"})

        for response in (by_query, by_category, search_route):
            titles = [card.title for card in response.context["cards"]]
            self.assertIn(match.title, titles)
            self.assertNotIn(other.title, titles)

    def test_pagination_keeps_the_active_filters(self):
        category = Category.objects.create(name="Guides")
        for index in range(15):
            self.make_post(f"Guide number {index:02d}", category=category)

        response = self.client.get(
            reverse("blog:post_list"), {"category": category.slug, "sort": "title"}
        )

        self.assertContains(response, "category=guides")
        self.assertContains(response, "sort=title")
        self.assertContains(response, "page=2")
        second_query: dict[str, str | int] = {
            "category": category.slug,
            "sort": "title",
            "page": 2,
        }
        second = self.client.get(reverse("blog:post_list"), second_query)
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, "Guide number")

    def test_invalid_filters_fall_back_safely(self):
        response = self.client.get(
            reverse("blog:post_list"),
            {"sort": "nonsense", "page": "abc", "q": "x" * 500},
        )

        self.assertEqual(response.status_code, 200)

    def test_only_public_tags_are_rendered_on_cards_and_in_the_cloud(self):
        public = Tag.objects.create(name="Public topic", is_curated=True)
        hidden = Tag.objects.create(name="Hidden topic", ai_suggested=True)
        self.post.tags.add(public, hidden)

        response = self.client.get(reverse("blog:post_list"))

        self.assertContains(response, "Public topic")
        self.assertNotContains(response, "Hidden topic")

    def test_disabled_blog_is_hidden_from_the_public_but_not_from_staff(self):
        BlogSettings.objects.update_or_create(pk=1, defaults={"enable_blog": False})
        staff = get_user_model().objects.create_user(
            username="journal-staff", email="journal-staff@example.com", is_staff=True
        )

        self.assertEqual(self.client.get(reverse("blog:post_list")).status_code, 404)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("blog:post_list")).status_code, 200)

    def test_no_demo_preview_labels_exist_anywhere(self):
        from django.conf import settings

        self.assertFalse(hasattr(settings, "LOCAL_DEMO_PREVIEWS_ENABLED"))
        generated = self.make_post(
            "Generated review post", status=PostStatus.IN_REVIEW, is_ai_generated=True
        )

        response = self.client.get(reverse("blog:post_list"))

        self.assertNotContains(response, generated.title)
        self.assertNotContains(response, "Local demo preview")
        self.assertNotContains(response, "Governance reviewed")


class ArticleDetailTests(JournalTestCase):
    def test_article_renders_sanitised_body_toc_share_and_author_box(self):
        post = self.make_post(
            "Structured article",
            body=(
                "<h2>First section</h2><p>One</p><h2>Second section</h2><p>Two</p>"
                "<h3>Third part</h3><p>Three</p>"
                '<script>alert(1)</script><p style="color:red">styled</p>'
                '<img src="https://tracker.example/x.png"><iframe src="https://www.youtube.com/embed/x"></iframe>'
            ),
        )

        response = self.client.get(post.get_absolute_url())

        self.assertContains(response, 'id="first-section"')
        self.assertContains(response, 'href="#second-section"')
        self.assertContains(response, "In this article")
        self.assertContains(response, "Share this article")
        self.assertContains(response, "twitter.com/intent/tweet")
        self.assertContains(response, "Written by")
        self.assertNotContains(response, "<script>alert")
        self.assertNotContains(response, "style=")
        self.assertNotContains(response, "tracker.example")
        self.assertNotContains(response, "<iframe")

    def test_render_time_sanitising_covers_bodies_that_bypassed_save(self):
        post = self.make_post("Updated behind the model's back")
        Post.objects.filter(pk=post.pk).update(
            body='<p>safe</p><script>alert("x")</script><a href="javascript:alert(1)">bad</a>'
        )

        response = self.client.get(post.get_absolute_url())

        self.assertContains(response, "<p>safe</p>")
        self.assertNotContains(response, "<script>alert")
        self.assertNotContains(response, "javascript:")

    def test_wide_tables_are_wrapped_in_a_keyboard_scroll_region(self):
        post = self.make_post(
            "Table article",
            body="<table><tr><th>A</th></tr><tr><td>1</td></tr></table>",
        )

        response = self.client.get(post.get_absolute_url())

        self.assertContains(
            response, 'class="article-table" role="region" tabindex="0"'
        )

    def test_previous_and_next_links_and_related_articles(self):
        older = self.make_post(
            "Older article", published_at=timezone.now() - timedelta(days=5)
        )
        shared = Tag.objects.create(name="Shared topic", is_curated=True)
        self.post.tags.add(shared)
        older.tags.add(shared)

        response = self.client.get(self.post.get_absolute_url())

        self.assertContains(response, "Related articles")
        self.assertContains(response, older.title)

    def test_non_live_posts_are_visible_to_their_author_as_a_preview(self):
        draft = self.make_post("Private draft", status=PostStatus.DRAFT)
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)

        self.client.force_login(self.author)
        response = self.client.get(draft.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview.")

    def test_views_are_counted_once_per_session(self):
        url = self.post.get_absolute_url()
        self.client.get(url)
        self.client.get(url)

        self.post.refresh_from_db()
        self.assertEqual(self.post.views_count, 1)

    def test_cover_image_only_renders_for_same_origin_paths(self):
        local = self.make_post("Local cover", hero_image="/media/cover.jpg")
        remote = self.make_post("Remote cover", hero_image="https://cdn.example/c.jpg")

        self.assertContains(
            self.client.get(local.get_absolute_url()), '<img src="/media/cover.jpg"'
        )
        self.assertNotContains(
            self.client.get(remote.get_absolute_url()),
            'src="https://cdn.example/c.jpg"',
        )

    def test_unknown_slug_is_404(self):
        self.assertEqual(
            self.client.get(
                reverse("blog:post_detail", kwargs={"slug": "nope"})
            ).status_code,
            404,
        )


class ArchiveAndFeedTests(JournalTestCase):
    def test_archive_pages_list_only_that_period(self):
        year = self.post.published_at.year
        month = self.post.published_at.month

        by_year = self.client.get(
            reverse("blog:post_archive_year", kwargs={"year": year})
        )
        by_month = self.client.get(
            reverse("blog:post_archive_month", kwargs={"year": year, "month": month})
        )
        empty = self.client.get(
            reverse("blog:post_archive_year", kwargs={"year": 2001})
        )

        self.assertIn(self.post.title, [c.title for c in by_year.context["cards"]])
        self.assertIn(self.post.title, [c.title for c in by_month.context["cards"]])
        self.assertEqual(empty.context["cards"], [])
        self.assertEqual(
            self.client.get(
                reverse("blog:post_archive_month", kwargs={"year": year, "month": 13})
            ).status_code,
            404,
        )

    def test_feeds_use_public_names_and_absolute_prefixed_links(self):
        rss = self.client.get(reverse("blog:feed_rss"))
        data = self.client.get(reverse("blog:feed_json")).json()

        self.assertEqual(rss.status_code, 200)
        self.assertContains(rss, "/blog/")
        self.assertContains(rss, "Jamie Writer")
        self.assertNotContains(rss, "journal-author@example.com")
        self.assertEqual(data["items"][0]["author"]["name"], "Jamie Writer")
        self.assertTrue(data["items"][0]["url"].startswith("http"))

    def test_sitemap_lists_only_live_indexable_posts(self):
        from apps.blog.sitemaps import PublishedBlogPostsSitemap

        hidden = self.make_post("Noindex post", noindex=True)
        draft = self.make_post("Draft post", status=PostStatus.DRAFT)

        items = list(PublishedBlogPostsSitemap().items())

        self.assertIn(self.post, items)
        self.assertNotIn(hidden, items)
        self.assertNotIn(draft, items)
