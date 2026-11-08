"""The comment moderation queue renders inside the Unfold admin shell."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment


class ModerationQueueShellTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="ms-author", email="ms-author@example.test"
        )
        self.moderator = user_model.objects.create_user(
            username="ms-moderator", email="ms-moderator@example.test", is_staff=True
        )
        self.moderator.user_permissions.add(
            Permission.objects.get(codename="change_comment"),
            Permission.objects.get(codename="view_comment"),
        )
        post = Post.objects.create(
            title="Shell moderated post",
            body="<p>A post with comments awaiting review.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
        )
        self.comment = Comment.objects.create(
            post=post, user=self.author, body="Check my channel", status="pending"
        )
        self.url = reverse("comments:moderation_queue")

    def test_queue_renders_in_the_admin_shell_with_dialog_triggers(self):
        self.client.force_login(self.moderator)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/staff_page.html")
        self.assertTemplateNotUsed(response, "base.html")
        self.assertContains(response, "Comment moderation")
        self.assertContains(response, "Check my channel")
        self.assertContains(response, 'id="dialog-slot"')
        for action in ("reject", "spam"):
            dialog = reverse(
                "comments:moderation_dialog",
                kwargs={"comment_id": self.comment.pk, "action": action},
            )
            self.assertContains(response, f'hx-get="{dialog}"')
        html = response.content.decode()
        for marker in ("site-container", "site-header", "bootstrap"):
            self.assertNotIn(marker, html)
        self.assertNotRegex(
            html, r'class="(?:[^"]*\s)?(?:btn|card|d-flex)(?:\s[^"]*)?"'
        )

    def test_viewers_without_change_permission_get_no_decision_controls(self):
        viewer = get_user_model().objects.create_user(
            username="ms-viewer", email="ms-viewer@example.test", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_comment"))
        self.client.force_login(viewer)

        response = self.client.get(self.url)

        self.assertContains(response, "deciding needs the")
        self.assertNotContains(response, 'name="action" value="approve"')
        self.assertNotContains(response, f"moderation/{self.comment.pk}/reject/dialog")

    def test_dialog_fallback_page_is_an_admin_page_with_the_same_form(self):
        self.client.force_login(self.moderator)
        url = reverse(
            "comments:moderation_dialog",
            kwargs={"comment_id": self.comment.pk, "action": "reject"},
        )

        response = self.client.get(url)

        self.assertTemplateUsed(response, "admin/staff_dialog_page.html")
        self.assertContains(response, "Reject this comment?")
        self.assertContains(response, "Check my channel")
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_queue_requires_a_staff_session(self):
        anonymous = Client().get(self.url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("login", anonymous["Location"])
        member = Client()
        member.force_login(self.author)
        self.assertEqual(member.get(self.url).status_code, 302)
