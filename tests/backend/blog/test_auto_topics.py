from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.blog.auto import (
    GenerationError,
    autoplan_topics,
    check_content,
    generate_post_from_topic,
)
from apps.blog.models import AutoTopic, Post, PostRevision, PostStatus
from apps.blog.tasks import process_queued_topics
from apps.core import ai_client


def article(words: int = 230) -> str:
    body = " ".join(["verified technical information"] * words)
    return f"<h2>Overview</h2><p>{body}</p><h2>Steps</h2><p>More.</p>"


class TopicGenerationTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="auto-staff", email="auto-staff@example.test", is_staff=True
        )
        self.topic = AutoTopic.objects.create(
            topic="Safe firmware update practices", created_by=self.staff
        )

    def test_success_creates_one_reviewable_draft_and_duplicate_delivery_is_idempotent(
        self,
    ):
        with patch(
            "apps.blog.auto.ai_client.generate_answer", return_value=article()
        ) as ai:
            post = generate_post_from_topic(self.topic)
            duplicate = generate_post_from_topic(self.topic)

        self.assertEqual(duplicate.pk, post.pk)
        self.assertEqual(ai.call_count, 1)
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        self.assertTrue(post.noindex)
        self.assertTrue(post.is_ai_generated)
        self.assertTrue(post.seo_description)
        self.assertTrue(post.summary)
        self.assertEqual(PostRevision.objects.filter(post=post).count(), 1)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, "succeeded")
        self.assertEqual(self.topic.post, post)

    def test_seo_fields_are_persisted(self):
        with patch("apps.blog.auto.ai_client.generate_answer", return_value=article()):
            post = generate_post_from_topic(self.topic)

        post.refresh_from_db()
        self.assertEqual(post.seo_title, self.topic.topic)
        self.assertIn("verified technical information", post.seo_description)

    def test_provider_failure_fails_closed_without_creating_content(self):
        with (
            patch(
                "apps.blog.auto.ai_client.generate_answer",
                side_effect=ai_client.AiClientError(
                    "AI_OPENAI_API_KEY is not configured"
                ),
            ),
            self.assertRaises(GenerationError),
        ):
            generate_post_from_topic(self.topic)

        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, "failed")
        self.assertIn("not configured", self.topic.last_error)
        self.assertFalse(Post.objects.exists())

    def test_thin_content_fails_then_a_retry_reuses_nothing_and_succeeds(self):
        with patch(
            "apps.blog.auto.ai_client.generate_answer",
            side_effect=["<h2>x</h2><p>too short</p>", article()],
        ):
            with self.assertRaises(GenerationError):
                generate_post_from_topic(self.topic)
            self.topic.refresh_from_db()
            self.assertEqual(self.topic.status, "failed")
            post = generate_post_from_topic(self.topic)

        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.topic.refresh_from_db()
        self.assertEqual((self.topic.status, self.topic.retry_count), ("succeeded", 2))

    def test_a_running_topic_cannot_be_claimed_twice(self):
        AutoTopic.objects.filter(pk=self.topic.pk).update(status="running")

        with self.assertRaises(RuntimeError):
            generate_post_from_topic(self.topic)

    def test_content_checks(self):
        for bad in ("<h2>x</h2><p>short</p>", "<p>" + "word " * 250 + "</p>"):
            with self.assertRaises(GenerationError):
                check_content(bad)
        with self.assertRaises(GenerationError):
            check_content("<h2>a</h2><p>" + "as an AI language model " * 100 + "</p>")
        check_content(article())

    def test_task_processes_queued_topics_and_skips_exhausted_ones(self):
        AutoTopic.objects.create(topic="Exhausted", status="failed", retry_count=9)

        with patch("apps.blog.auto.ai_client.generate_answer", return_value=article()):
            done = process_queued_topics(limit=5)
            again = process_queued_topics(limit=5)

        self.assertEqual((done, again), (1, 0))
        self.assertEqual(Post.objects.count(), 1)

    def test_autoplan_queues_new_unique_topics(self):
        AutoTopic.objects.create(topic="Existing idea")

        with patch(
            "apps.blog.auto.ai_client.generate_answer",
            return_value="1. Existing idea\n- Fresh idea one\n* Fresh idea two\n",
        ):
            queued = autoplan_topics("firmware", count=5)

        self.assertEqual(
            [t.topic for t in queued], ["Fresh idea one", "Fresh idea two"]
        )

    def test_autoplan_without_a_provider_queues_nothing(self):
        with patch(
            "apps.blog.auto.ai_client.generate_answer",
            side_effect=ai_client.AiClientError("not configured"),
        ):
            self.assertEqual(autoplan_topics("firmware"), [])
