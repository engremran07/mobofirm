from django.test import SimpleTestCase

from apps.comments.rendering import render_comment_body


class CommentBodyRenderingTests(SimpleTestCase):
    def test_external_links_open_safely_but_same_site_links_stay_in_place(self):
        rendered = render_comment_body(
            (
                '<p><a href="https://outside.example/path">External</a> '
                '<a href="https://mobofirm.example/help">Internal</a> '
                '<a href="/account/">Relative</a> '
                '<a href="mailto:support@mobofirm.example">Email</a></p>'
            ),
            host="mobofirm.example",
            secure=True,
        )

        self.assertIn(
            '<a href="https://outside.example/path" rel="nofollow noopener noreferrer" '
            'target="_blank">',
            rendered,
        )
        self.assertIn(
            '<a href="https://mobofirm.example/help" rel="nofollow noopener">',
            rendered,
        )
        self.assertIn('<a href="/account/" rel="nofollow noopener">', rendered)
        self.assertIn(
            '<a href="mailto:support@mobofirm.example" rel="nofollow noopener">',
            rendered,
        )

    def test_different_port_is_external_and_unsafe_markup_remains_sanitized(self):
        rendered = render_comment_body(
            (
                '<p><a href="http://localhost:9000/help">Other service</a></p>'
                '<script>alert(1)</script><img src="https://tracker.example/pixel">'
            ),
            host="localhost:8000",
            secure=False,
        )

        self.assertIn('target="_blank"', rendered)
        self.assertIn("noopener noreferrer", rendered)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("tracker.example", rendered)
