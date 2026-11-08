"""Comments-owned moderation dialogs: reject and mark as spam, with a note."""

from __future__ import annotations

from typing import TypedDict

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment


class HtmxExtra(TypedDict):
    HTTP_HX_REQUEST: str


HTMX = HtmxExtra(HTTP_HX_REQUEST="true")


class ModerationDialogTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="md-author", email="md-author@example.test"
        )
        self.moderator = user_model.objects.create_user(
            username="md-moderator", email="md-moderator@example.test", is_staff=True
        )
        self.moderator.user_permissions.add(
            Permission.objects.get(codename="change_comment")
        )
        post = Post.objects.create(
            title="Moderated dialog post",
            body="<p>A post with moderated comments.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
        )
        self.comment = Comment.objects.create(
            post=post, user=self.author, body="Buy cheap watches", status="pending"
        )
        self.client.force_login(self.moderator)

    def url(self, action: str) -> str:
        return reverse(
            "comments:moderation_dialog",
            kwargs={"comment_id": self.comment.pk, "action": action},
        )

    def test_reject_dialog_shows_the_comment_and_records_the_note(self):
        page = self.client.get(self.url("reject"), **HTMX)
        self.assertContains(page, "Reject this comment?")
        self.assertContains(page, "Buy cheap watches")
        self.assertContains(page, "csrfmiddlewaretoken")

        response = self.client.post(self.url("reject"), {"reason": "Off topic"}, **HTMX)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], reverse("comments:moderation_queue"))
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.REJECTED)
        self.assertEqual(self.comment.moderation_flags["decision"]["note"], "Off topic")

    def test_spam_dialog_marks_spam_without_a_note(self):
        response = self.client.post(self.url("spam"), {}, **HTMX)
        self.assertEqual(response.status_code, 204)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.SPAM)

    def test_overlong_note_is_rejected_inside_the_dialog(self):
        response = self.client.post(self.url("reject"), {"reason": "x" * 1001}, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mf-ui-dialog__error")
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.PENDING)

    def test_approve_is_not_a_dialog_and_unknown_actions_are_not_found(self):
        self.assertEqual(self.client.get(self.url("approve"), **HTMX).status_code, 404)
        self.assertEqual(self.client.get(self.url("delete"), **HTMX).status_code, 404)

    def test_permission_and_csrf_are_enforced(self):
        member = Client()
        member.force_login(
            get_user_model().objects.create_user(
                username="md-member", email="md-member@example.test", is_staff=True
            )
        )
        self.assertEqual(member.get(self.url("reject"), **HTMX).status_code, 403)
        self.assertEqual(member.post(self.url("reject"), **HTMX).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.moderator)
        self.assertEqual(strict.post(self.url("reject"), **HTMX).status_code, 403)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.status, Comment.Status.PENDING)

    def test_queue_page_opens_the_dialogs(self):
        page = self.client.get(reverse("comments:moderation_queue"))
        self.assertContains(page, f'hx-get="{self.url("reject")}"')
        self.assertContains(page, f'hx-get="{self.url("spam")}"')
        self.assertContains(page, 'value="approve"')
