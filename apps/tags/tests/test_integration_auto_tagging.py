"""
DISABLED: This test suite depends on archived enhanced services.
See apps/core/versions/ for the original implementation.

To restore this test:
1. Restore apps/tags/services_enhanced.py from apps/core/versions/
2. Update imports to use the restored module
3. Re-enable this test suite
"""

import pytest

pytest.skip(allow_module_level=True)


class TestIntegrationAutoTagging(TestCase):  # noqa: F821  # type: ignore[name-defined]
    def setUp(self):
        self.user = User.objects.create_user(  # noqa: F821  # type: ignore[name-defined]
            email="t@t.com", username="t", password="pw"
        )

    def test_comment_does_not_auto_tag(self):
        post = Post.objects.create(  # noqa: F821  # type: ignore[name-defined]
            title="Comment Tag Test",
            slug="comment-tag-test",
            summary="summary",
            body="body",
            author=self.user,
            status=PostStatus.PUBLISHED,  # noqa: F821  # type: ignore[name-defined]
        )
        initial_count = post.tags.count()

        # Create a comment that contains potential tag words
        from apps.comments.models import Comment

        Comment.objects.create(
            post=post,
            user=self.user,
            body="This mentions Django and SEO extensively",
            status=Comment.Status.APPROVED,
        )
        post.refresh_from_db()
        assert post.tags.count() == initial_count, (
            "Comments should not change auto-generated tags"
        )

    def test_suggest_only_mode_does_not_attach_tags(self):
        # Enable suggest_only in SEO settings
        s = SeoAutomationSettings.get_solo()  # noqa: F821  # type: ignore[name-defined]
        original = s.suggest_only
        s.suggest_only = True
        s.save()

        try:
            post = Post.objects.create(  # noqa: F821  # type: ignore[name-defined]
                title="Suggest Only Test",
                slug="suggest-only-test",
                summary="SEO suggestions should not attach tags when suggest_only is enabled",
                body="SEO suggestions should include seo and django keywords",
                author=self.user,
                status=PostStatus.PUBLISHED,  # noqa: F821  # type: ignore[name-defined]
            )
            post.refresh_from_db()
            assert post.tags.count() == 0, (
                "With suggest_only enabled, tags must not be auto-attached"
            )
        finally:
            s.suggest_only = original
            s.save()

    def test_ai_auto_create_tags_creates_new_tags(self):
        text = "Quantum computing and AI advances"

        with patch(  # noqa: F821  # type: ignore[name-defined]
            "apps.tags.services_enhanced.ai_client.suggest_tags",
            return_value=["Quantum", "AI"],
        ):
            tags = auto_tag_content(text, auto_create=True, max_tags=5, min_score=0.1)  # noqa: F821  # type: ignore[name-defined]
            assert len(tags) > 0, "Expected auto-created tags from AI suggestions"
            created = Tag.objects.filter(  # noqa: F821  # type: ignore[name-defined]
                ai_suggested=True, name__in=[t.name for t in tags]
            )
            assert created.count() == len(tags)
            for t in tags:
                assert t.ai_suggested
                assert t.ai_score > 0
