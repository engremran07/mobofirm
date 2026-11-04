from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus


class BlogFrontendTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username="journal-author",
            email="journal-author@example.com",
            password="test-password",
        )
        self.post = Post.objects.create(
            title="A practical firmware guide",
            summary="How to identify the right device firmware.",
            body="<p>A practical explanation with verified technical context.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
        )

    def test_journal_lists_published_posts(self):
        response = self.client.get(reverse("blog:post_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertContains(response, "Search articles")

    def test_journal_list_htmx_request_returns_only_the_posts_fragment(self):
        response = self.client.get(
            reverse("blog:post_list"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertNotContains(response, "<!doctype html>")

    def test_post_detail_renders_sanitized_article_and_comment_signin(self):
        response = self.client.get(
            reverse("blog:post_detail", kwargs={"slug": self.post.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.post.title)
        self.assertContains(response, "A practical explanation")
        self.assertContains(response, "Sign in")

    def test_account_entry_pages_render_real_forms(self):
        login = self.client.get(reverse("users:account_login"))
        signup = self.client.get(reverse("users:account_signup"))

        self.assertEqual(login.status_code, 200)
        self.assertContains(login, "Sign in")
        self.assertContains(login, "csrfmiddlewaretoken")
        self.assertEqual(signup.status_code, 200)
        self.assertContains(signup, "Create your account")
