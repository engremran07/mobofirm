from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.blog.models import Post, PostTranslation
from apps.blog.rendering import plain_excerpt, render_article
from apps.core.utils.sanitize import sanitize_html


class SanitizerTests(SimpleTestCase):
    def test_allow_list_matches_what_the_csp_can_render(self):
        cleaned = sanitize_html(
            '<h2 class="x" style="color:red">Title</h2>'
            "<pre><code>x = 1</code></pre>"
            '<figure><img src="/media/a.png" alt="a"><figcaption>c</figcaption></figure>'
        )

        self.assertIn('<h2 class="x">Title</h2>', cleaned)
        self.assertIn("<pre><code>x = 1</code></pre>", cleaned)
        self.assertIn('<img src="/media/a.png" alt="a">', cleaned)
        self.assertNotIn("style", cleaned)

    def test_h1_and_iframes_and_foreign_images_are_removed(self):
        cleaned = sanitize_html(
            "<h1>Top</h1>"
            '<iframe src="https://www.youtube.com/embed/x"></iframe>'
            '<img src="https://evil.example/t.png">'
            '<img src="//evil.example/t.png">'
        )

        self.assertNotIn("<h1", cleaned)
        self.assertNotIn("iframe", cleaned)
        self.assertNotIn("<img", cleaned)

    def test_dangerous_urls_and_scripts_are_stripped(self):
        cleaned = sanitize_html(
            '<a href="javascript:alert(1)" onclick="x()">x</a><script>alert(1)</script>'
            '<a href="https://ok.example">ok</a>'
        )

        self.assertNotIn("javascript:", cleaned)
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("script", cleaned)
        self.assertIn('href="https://ok.example"', cleaned)
        self.assertIn("noopener", cleaned)

    def test_empty_input(self):
        self.assertEqual(sanitize_html(""), "")


class RenderArticleTests(SimpleTestCase):
    def test_headings_get_unique_anchors_and_a_toc_when_long_enough(self):
        article = render_article(
            "<h2>Setup</h2><p>a</p><h2>Setup</h2><p>b</p><h3>Detail</h3>"
        )

        self.assertEqual(
            [entry.anchor for entry in article.toc], ["setup", "setup-2", "detail"]
        )
        self.assertIn('<h2 id="setup">Setup</h2>', article.html)
        self.assertIn('<h2 id="setup-2">Setup</h2>', article.html)

    def test_short_articles_have_no_toc(self):
        self.assertEqual(render_article("<h2>One</h2><p>x</p>").toc, [])

    def test_tables_are_wrapped_for_keyboard_scrolling(self):
        article = render_article("<table><tr><td>1</td></tr></table>")

        self.assertIn('role="region" tabindex="0"', article.html)
        self.assertIn("<table>", article.html)

    def test_render_time_resanitising_and_word_count(self):
        article = render_article("<p>two words</p><script>bad()</script>")

        self.assertNotIn("script", article.html)
        self.assertEqual(article.word_count, 2)

    def test_excerpt_unescapes_entities_and_truncates_on_a_word(self):
        self.assertEqual(plain_excerpt("<p>A &amp; B</p>"), "A & B")
        excerpt = plain_excerpt("word " * 100, 20)
        self.assertTrue(excerpt.endswith("…"))
        self.assertLessEqual(len(excerpt), 20)


class StoredContentTests(TestCase):
    def test_posts_and_translations_are_sanitised_on_save(self):
        author = get_user_model().objects.create_user(
            username="san-author", email="san-author@example.test"
        )
        post = Post.objects.create(
            title="Sanitised post",
            body='<p style="x">ok</p><script>x()</script><iframe src="https://a.b"></iframe>',
            author=author,
        )
        translation = PostTranslation.objects.create(
            post=post, language="fr", body="<p>bon</p><script>x()</script>"
        )

        self.assertNotIn("script", post.body)
        self.assertNotIn("iframe", post.body)
        self.assertNotIn("style", post.body)
        self.assertNotIn("script", translation.body)
