"""Blog-owned workflow dialogs: publish, schedule, archive."""

from __future__ import annotations

from datetime import timedelta
from typing import TypedDict

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus


class HtmxExtra(TypedDict):
    HTTP_HX_REQUEST: str


HTMX = HtmxExtra(HTTP_HX_REQUEST="true")


class PostWorkflowDialogTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="wd-author", email="wd-author@example.test"
        )
        self.editor = user_model.objects.create_user(
            username="wd-editor", email="wd-editor@example.test", is_staff=True
        )
        self.editor.user_permissions.add(Permission.objects.get(codename="change_post"))
        self.post = Post.objects.create(
            title="Dialog post",
            body="<p>Body for the dialog workflow test post.</p>",
            author=self.author,
        )
        self.client.force_login(self.editor)

    def url(self, action: str, post: Post | None = None) -> str:
        return reverse(
            "blog:post_workflow_dialog",
            kwargs={"slug": (post or self.post).slug, "action": action},
        )

    def test_publish_dialog_renders_and_publishes_on_post(self):
        page = self.client.get(self.url("publish"), **HTMX)
        self.assertContains(page, "Publish this post?")
        self.assertContains(page, "Dialog post")
        self.assertContains(page, "csrfmiddlewaretoken")
        self.assertNotContains(page, "btn-danger")

        response = self.client.post(self.url("publish"), **HTMX)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], reverse("blog:manage_posts"))
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.PUBLISHED)

    def test_archive_dialog_is_destructive_and_archives(self):
        self.post.status = PostStatus.PUBLISHED
        self.post.save(update_fields=["status"])
        page = self.client.get(self.url("archive"), **HTMX)
        self.assertContains(page, "btn-danger")
        self.assertContains(page, 'data-dialog-backdrop="static"')

        self.assertEqual(self.client.post(self.url("archive"), **HTMX).status_code, 204)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.ARCHIVED)

    def test_schedule_dialog_validates_the_time_inside_the_dialog(self):
        past = (timezone.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        invalid = self.client.post(self.url("schedule"), {"when": past}, **HTMX)
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Choose a time in the future.")
        self.post.refresh_from_db()
        self.assertNotEqual(self.post.status, PostStatus.SCHEDULED)

        future = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        ok = self.client.post(self.url("schedule"), {"when": future}, **HTMX)
        self.assertEqual(ok.status_code, 204)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.SCHEDULED)

    def test_unknown_action_and_unmanaged_post_are_not_found(self):
        self.assertEqual(self.client.get(self.url("explode"), **HTMX).status_code, 404)
        other = Client()
        stranger = get_user_model().objects.create_user(
            username="wd-stranger", email="wd-stranger@example.test"
        )
        other.force_login(stranger)
        self.assertEqual(other.get(self.url("publish"), **HTMX).status_code, 404)

    def test_author_without_publish_permission_sees_the_rule_in_the_dialog(self):
        self.client.force_login(self.author)
        response = self.client.post(self.url("publish"), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff publishing permission is required.")
        self.post.refresh_from_db()
        self.assertNotEqual(self.post.status, PostStatus.PUBLISHED)

    def test_anonymous_is_sent_to_sign_in_and_csrf_is_enforced(self):
        self.assertEqual(Client().get(self.url("publish"), **HTMX).status_code, 204)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.editor)
        self.assertEqual(strict.post(self.url("publish"), **HTMX).status_code, 403)

    def test_manage_page_opens_the_dialogs_without_inline_forms(self):
        self.post.status = PostStatus.IN_REVIEW
        self.post.save(update_fields=["status"])
        page = self.client.get(reverse("blog:manage_posts"))
        self.assertContains(page, f'hx-get="{self.url("publish")}"')
        self.assertContains(page, f'hx-get="{self.url("archive")}"')
        self.assertContains(page, 'hx-target="#dialog-slot"')
