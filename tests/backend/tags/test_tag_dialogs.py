"""Tags-owned curation dialogs: reject a suggestion, merge two tags."""

from __future__ import annotations

from typing import TypedDict
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.tags.models import Tag
from apps.tags.services import automation


class HtmxExtra(TypedDict):
    HTTP_HX_REQUEST: str


HTMX = HtmxExtra(HTTP_HX_REQUEST="true")


class TagDialogTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="td-staff", email="td-staff@example.test", is_staff=True
        )
        self.staff.user_permissions.add(Permission.objects.get(codename="change_tag"))
        self.author = user_model.objects.create_user(
            username="td-author", email="td-author@example.test"
        )
        self.suggestion = Tag.objects.create(name="Odd suggestion", ai_suggested=True)
        self.client.force_login(self.staff)

    def reject_url(self, tag: Tag | None = None) -> str:
        return reverse(
            "tags:reject_dialog", kwargs={"tag_id": (tag or self.suggestion).pk}
        )

    def test_reject_dialog_confirms_then_retires_the_suggestion(self):
        page = self.client.get(self.reject_url(), **HTMX)
        self.assertContains(page, "Reject this suggested tag?")
        self.assertContains(page, "Odd suggestion")
        self.assertContains(page, "mf-ui-btn--danger")
        self.assertNotContains(page, "btn-danger")

        response = self.client.post(self.reject_url(), **HTMX)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], reverse("tags:manage"))
        self.suggestion.refresh_from_db()
        self.assertFalse(self.suggestion.is_curated or self.suggestion.is_active)
        self.assertFalse(self.suggestion.ai_suggested)

    def test_rejected_suggestion_leaves_the_queue_and_is_never_auto_curated(self):
        self.client.post(self.reject_url(), **HTMX)

        queue = self.client.get(reverse("tags:manage"))
        self.assertEqual(list(queue.context["suggestions"]), [])
        self.assertEqual(queue.context["totals"]["pending"], 0)
        self.assertEqual(self.client.get(self.reject_url(), **HTMX).status_code, 404)
        with mock.patch("apps.tags.services.automation.AUTO_CURATE_MIN_POSTS", 0):
            promoted = automation.curate_suggested_tags()
        self.assertNotIn(self.suggestion.pk, promoted)

    def test_reject_only_applies_to_pending_suggestions(self):
        curated = Tag.objects.create(name="Curated", is_curated=True)
        self.assertEqual(
            self.client.get(self.reject_url(curated), **HTMX).status_code, 404
        )

    def test_permission_csrf_and_anonymous_handling(self):
        member = Client()
        member.force_login(
            get_user_model().objects.create_user(
                username="td-member", email="td-member@example.test"
            )
        )
        self.assertEqual(member.post(self.reject_url(), **HTMX).status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.staff)
        self.assertEqual(strict.post(self.reject_url(), **HTMX).status_code, 403)
        self.assertEqual(Client().get(self.reject_url(), **HTMX).status_code, 204)
        self.suggestion.refresh_from_db()
        self.assertTrue(self.suggestion.is_active or not self.suggestion.is_curated)

    def test_merge_dialog_shows_impact_and_merges_atomically(self):
        source = Tag.objects.create(name="Boot loader", is_curated=True)
        target = Tag.objects.create(name="Bootloader", is_curated=True)
        post = Post.objects.create(
            title="Merged by dialog",
            body="<p>Body with enough words to publish.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
        )
        post.tags.add(source)
        url = reverse("tags:merge_dialog")
        query = {"source": source.slug, "target": target.slug}

        page = self.client.get(url, query, **HTMX)
        self.assertContains(page, "This will change <strong>1 article</strong>")
        self.assertContains(page, f"?source={source.slug}&amp;target={target.slug}")

        response = self.client.post(
            f"{url}?source={source.slug}&target={target.slug}", **HTMX
        )

        self.assertEqual(response.status_code, 204)
        source.refresh_from_db()
        self.assertEqual(source.merge_into, target)
        self.assertEqual(list(post.tags.all()), [target])

    def test_merge_dialog_rejects_self_merges_and_missing_tags(self):
        tag = Tag.objects.create(name="Same tag", is_curated=True)
        url = reverse("tags:merge_dialog")
        self_merge = self.client.post(
            f"{url}?source={tag.slug}&target={tag.slug}", **HTMX
        )
        self.assertEqual(self_merge.status_code, 200)
        self.assertContains(self_merge, "A tag cannot be merged into itself.")
        self.assertEqual(self.client.get(url, **HTMX).status_code, 404)
        self.assertEqual(
            self.client.get(
                url, {"source": tag.slug, "target": "nope"}, **HTMX
            ).status_code,
            404,
        )

    def test_manage_page_opens_both_dialogs(self):
        page = self.client.get(reverse("tags:manage"))
        self.assertContains(page, f'hx-get="{self.reject_url()}"')
        self.assertContains(page, f'hx-get="{reverse("tags:merge_dialog")}"')
        self.assertContains(page, 'hx-target="#dialog-slot"')
