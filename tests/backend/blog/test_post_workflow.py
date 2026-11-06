from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.blog.services import workflow


class PostWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="wf-author", email="wf-author@example.test"
        )
        self.other = user_model.objects.create_user(
            username="wf-other", email="wf-other@example.test"
        )
        self.editor = user_model.objects.create_user(
            username="wf-editor", email="wf-editor@example.test", is_staff=True
        )
        self.editor.user_permissions.add(Permission.objects.get(codename="change_post"))
        self.post = Post.objects.create(
            title="Workflow post",
            body="<p>Body for the workflow test post.</p>",
            author=self.author,
        )

    def test_author_requests_review_and_staff_publish_without_four_eyes_rules(self):
        reviewed = workflow.request_review(self.post, user=self.author)
        self.assertEqual(reviewed.status, PostStatus.IN_REVIEW)

        published = workflow.publish(self.post, user=self.editor)

        self.post.refresh_from_db()
        self.assertEqual(published.status, PostStatus.PUBLISHED)
        self.assertTrue(self.post.is_published)
        self.assertIsNotNone(self.post.published_at)

    def test_staff_who_authored_a_post_may_publish_it_themselves(self):
        own = Post.objects.create(
            title="Editor's own post",
            body="<p>Written and published by one editor.</p>",
            author=self.editor,
        )

        workflow.request_review(own, user=self.editor)
        workflow.publish(own, user=self.editor)

        own.refresh_from_db()
        self.assertEqual(own.status, PostStatus.PUBLISHED)

    def test_only_the_author_or_staff_may_request_review(self):
        with self.assertRaises(PermissionDenied):
            workflow.request_review(self.post, user=self.other)
        with self.assertRaises(PermissionDenied):
            workflow.request_review(self.post, user=None)

        workflow.request_review(self.post, user=self.editor)

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.IN_REVIEW)

    def test_publishing_needs_staff_with_the_change_permission(self):
        staff_without_permission = get_user_model().objects.create_user(
            username="wf-staff-no-perm", email="wf-no-perm@example.test", is_staff=True
        )

        for actor in (self.author, self.other, staff_without_permission, None):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                workflow.publish(self.post, user=actor)
            with self.assertRaises(PermissionDenied):
                workflow.archive(self.post, user=actor)

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.DRAFT)

    def test_scheduling_requires_an_aware_time_and_a_publishable_status(self):
        when = timezone.now() + timedelta(days=1)

        scheduled = workflow.schedule(self.post, when, user=self.editor)

        self.assertEqual(scheduled.status, PostStatus.SCHEDULED)
        self.post.refresh_from_db()
        self.assertEqual(self.post.publish_at, when)
        with self.assertRaises(ValidationError):
            workflow.schedule(
                self.post, timezone.now().replace(tzinfo=None), user=self.editor
            )
        workflow.archive(self.post, user=self.editor)
        with self.assertRaises(ValidationError):
            workflow.publish(self.post, user=self.editor)

    def test_empty_posts_cannot_enter_review_and_published_posts_cannot_re_enter(self):
        empty = Post.objects.create(
            title="Short body post",
            body="<p>Valid body text here.</p>",
            author=self.author,
        )
        Post.objects.filter(pk=empty.pk).update(body="")
        empty.refresh_from_db()

        with self.assertRaises(ValidationError):
            workflow.request_review(empty, user=self.author)
        workflow.publish(self.post, user=self.editor)
        with self.assertRaises(ValidationError):
            workflow.request_review(self.post, user=self.author)

    def test_review_request_is_idempotent(self):
        workflow.request_review(self.post, user=self.author)
        first_updated = Post.objects.get(pk=self.post.pk).updated_at

        again = workflow.request_review(self.post, user=self.author)

        self.assertEqual(again.status, PostStatus.IN_REVIEW)
        self.assertEqual(Post.objects.get(pk=self.post.pk).updated_at, first_updated)

    def test_workflow_api_maps_failures_to_client_errors(self):
        client = Client()
        url = reverse("blog:api_workflow", kwargs={"slug": self.post.slug})

        client.force_login(self.other)
        forbidden = client.post(url, {"action": "publish"})
        denied_review = client.post(url, {"action": "request_review"})
        client.force_login(self.author)
        ok = client.post(url, {"action": "request_review"})
        client.force_login(self.editor)
        bad_time = client.post(url, {"action": "schedule", "when": "not-a-date"})
        published = client.post(url, {"action": "publish"})
        invalid_status = client.post(url, {"action": "publish"})

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(denied_review.status_code, 403)
        self.assertEqual(ok.json()["status"], PostStatus.IN_REVIEW)
        self.assertEqual(bad_time.status_code, 400)
        self.assertEqual(published.json()["status"], PostStatus.PUBLISHED)
        self.assertEqual(invalid_status.status_code, 400)

    def test_admin_actions_use_the_same_rules(self):
        client = Client()
        client.force_login(self.editor)
        url = reverse("admin:blog_post_changelist")

        client.post(
            url,
            {
                "action": "submit_selected_for_review",
                "_selected_action": [str(self.post.pk)],
                "index": "0",
            },
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.IN_REVIEW)

        client.post(
            url,
            {
                "action": "publish_selected",
                "_selected_action": [str(self.post.pk)],
                "index": "0",
            },
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.PUBLISHED)

    def test_bulk_publish_view_only_publishes_for_permitted_staff(self):
        client = Client()
        url = reverse("blog:bulk_publish")

        client.force_login(self.author)
        client.post(url, {"post_id": [str(self.post.pk)]})
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.DRAFT)

        client.force_login(self.editor)
        client.post(url, {"post_id": [str(self.post.pk)]})
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.PUBLISHED)
