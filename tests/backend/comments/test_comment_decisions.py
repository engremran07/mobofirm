from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment
from apps.comments.services.decisions import moderate_comment


class CommentDecisionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="cm-author", email="cm-author@example.test"
        )
        self.moderator = user_model.objects.create_user(
            username="cm-moderator", email="cm-moderator@example.test", is_staff=True
        )
        self.moderator.user_permissions.add(
            Permission.objects.get(codename="change_comment")
        )
        post = Post.objects.create(
            title="Moderated post",
            body="<p>A post with moderated comments.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
        )
        self.comment = Comment.objects.create(
            post=post, user=self.author, body="Please moderate me", status="pending"
        )

    def test_decisions_set_the_status_and_leave_an_audit_entry(self):
        moderate_comment(self.comment, "approve", self.moderator, note="Looks fine")

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.APPROVED)
        self.assertTrue(self.comment.is_approved)
        decision = self.comment.moderation_flags["decision"]
        self.assertEqual(decision["action"], "approve")
        self.assertEqual(decision["by"], self.moderator.pk)
        self.assertEqual(decision["note"], "Looks fine")
        self.assertTrue(decision["at"])

        moderate_comment(self.comment, "spam", self.moderator)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.SPAM)
        self.assertFalse(self.comment.is_approved)

    def test_invalid_decisions_and_unauthorised_actors_are_refused(self):
        with self.assertRaises(ValidationError):
            moderate_comment(self.comment, "delete", self.moderator)
        for actor in (self.author, None):
            with self.assertRaises(PermissionDenied):
                moderate_comment(self.comment, "approve", actor)

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.PENDING)

    def test_moderation_panel_endpoint_uses_the_service(self):
        client = Client()
        url = reverse("comments:moderation_action")

        client.force_login(self.moderator)
        bad = client.post(url, {"comment_id": self.comment.pk, "action": "nope"})
        good = client.post(url, {"comment_id": self.comment.pk, "action": "reject"})

        self.assertEqual(bad.status_code, 400)
        self.assertEqual(good.status_code, 302)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.REJECTED)

    def test_moderation_panel_requires_staff_with_the_permission(self):
        staff_without_permission = get_user_model().objects.create_user(
            username="cm-plain-staff", email="cm-plain@example.test", is_staff=True
        )
        client = Client()
        client.force_login(staff_without_permission)

        response = client.post(
            reverse("comments:moderation_action"),
            {"comment_id": self.comment.pk, "action": "approve"},
        )

        self.assertEqual(response.status_code, 403)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.PENDING)

    def test_admin_actions_moderate_selected_comments(self):
        client = Client()
        client.force_login(self.moderator)

        client.post(
            reverse("admin:comments_comment_changelist"),
            {
                "action": "approve_selected",
                "_selected_action": [str(self.comment.pk)],
                "index": "0",
            },
        )

        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.APPROVED)
