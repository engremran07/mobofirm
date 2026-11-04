from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.blog.auto import generate_post_from_topic, reconcile_settings_with_post
from apps.blog.models import AutoTopic, Post, PostRevision, PostStatus
from apps.governance.models import ReviewCase


def _article(words: int) -> str:
    return "<p>" + " ".join(["verified technical information"] * words) + "</p>"


class BlogAutomationGovernanceTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username="automated-author",
            email="automated-author@example.com",
            password="test-password",
        )
        self.topic = AutoTopic.objects.create(
            topic="Safe firmware update practices",
            created_by=self.author,
        )

    def test_successful_generation_enters_review_and_duplicate_delivery_is_idempotent(
        self,
    ):
        with (
            patch(
                "apps.blog.auto.ai_client.generate_answer",
                return_value=_article(220),
            ),
            patch(
                "apps.blog.auto.ai_client.generate_excerpt",
                return_value="A reviewed summary.",
            ),
            patch(
                "apps.blog.auto.ai_client.moderate_text",
                return_value={"toxicity_score": 0.0, "label": "low"},
            ),
            patch(
                "apps.blog.auto.ai_client.generate_title",
                return_value="Reviewed title",
            ),
            patch(
                "apps.blog.auto.ai_client.generate_seo_description",
                return_value="Reviewed description.",
            ),
            patch(
                "apps.tags.services.heuristics.suggest_tags_from_text",
                return_value=[],
            ),
        ):
            post = generate_post_from_topic(self.topic)
            duplicate = generate_post_from_topic(self.topic)

        self.assertEqual(duplicate.pk, post.pk)
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        self.assertEqual(PostRevision.objects.filter(post=post).count(), 1)
        case = ReviewCase.objects.get(object_id=str(post.pk))
        self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)

    def test_failed_generation_remains_draft_and_retry_reuses_same_post(self):
        with (
            patch(
                "apps.blog.auto.ai_client.generate_answer",
                side_effect=[_article(20), _article(220)],
            ),
            patch(
                "apps.blog.auto.ai_client.generate_excerpt",
                return_value="A reviewed summary.",
            ),
            patch(
                "apps.blog.auto.ai_client.moderate_text",
                return_value={"toxicity_score": 0.0, "label": "low"},
            ),
            patch(
                "apps.blog.auto.ai_client.generate_title",
                return_value="Reviewed title",
            ),
            patch(
                "apps.blog.auto.ai_client.generate_seo_description",
                return_value="Reviewed description.",
            ),
            patch(
                "apps.tags.services.heuristics.suggest_tags_from_text",
                return_value=[],
            ),
        ):
            failed_post = generate_post_from_topic(self.topic)
            self.topic.refresh_from_db()
            retried_post = generate_post_from_topic(self.topic)

        self.assertEqual(failed_post.pk, retried_post.pk)
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(retried_post.status, PostStatus.IN_REVIEW)
        self.assertEqual(retried_post.version, 2)
        self.assertEqual(
            ReviewCase.objects.filter(object_id=str(retried_post.pk)).count(), 1
        )

    def test_settings_reconciliation_disables_comments_when_site_setting_is_off(self):
        post = Post.objects.create(
            title="Settings reconciliation",
            body="<p>Draft content for the test.</p>",
            author=self.author,
        )

        with (
            patch(
                "apps.blog.auto._get_site_settings_snapshot",
                return_value={"enable_blog_comments": False},
            ) as get_settings_snapshot,
            patch("apps.blog.auto.tag_services.auto_tag_post"),
        ):
            reconcile_settings_with_post(post)

        get_settings_snapshot.assert_called_once_with()
        post.refresh_from_db()
        self.assertFalse(post.allow_comments)
