from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, Post, PostLike, PostStatus
from apps.blog.tasks import publish_due_scheduled_posts
from apps.tags.models import Tag


class EditorialTestCase(TestCase):
    def setUp(self):
        user_model = get_user_model()
        BlogSettings.objects.update_or_create(
            pk=1, defaults={"enable_blog": True, "allow_user_blog_posts": False}
        )
        self.author = user_model.objects.create_user(
            username="ed-author", email="ed-author@example.test", role="author"
        )
        self.member = user_model.objects.create_user(
            username="ed-member", email="ed-member@example.test"
        )
        self.staff = user_model.objects.create_user(
            username="ed-staff", email="ed-staff@example.test", is_staff=True
        )
        self.staff.user_permissions.add(Permission.objects.get(codename="change_post"))
        self.draft = Post.objects.create(
            title="Editorial draft",
            body="<p>Draft body that is long enough.</p>",
            author=self.author,
        )

    def form_data(self, **extra):
        data = {
            "title": "A brand new article",
            "summary": "Short summary",
            "body": "<p>New article body text that is long enough.</p>",
            "allow_comments": "on",
        }
        data.update(extra)
        return data


class EditorialPermissionTests(EditorialTestCase):
    def test_members_cannot_reach_the_workspace_unless_user_posting_is_enabled(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("blog:manage_posts")).status_code, 404)
        self.assertEqual(self.client.get(reverse("blog:post_create")).status_code, 404)

        BlogSettings.objects.filter(pk=1).update(allow_user_blog_posts=True)
        self.assertEqual(self.client.get(reverse("blog:manage_posts")).status_code, 200)

    def test_authors_edit_their_own_drafts_but_not_live_posts(self):
        self.client.force_login(self.author)
        url = reverse("blog:post_create")

        response = self.client.post(
            f"{url}?edit={self.draft.slug}",
            self.form_data(title="Retitled draft", edit=self.draft.slug),
        )
        self.draft.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.draft.title, "Retitled draft")
        self.assertEqual(self.draft.version, 2)

        Post.objects.filter(pk=self.draft.pk).update(
            status=PostStatus.PUBLISHED, publish_at=timezone.now()
        )
        blocked = self.client.get(f"{url}?edit={self.draft.slug}")
        self.assertRedirects(blocked, reverse("blog:manage_posts"))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, "Retitled draft")

    def test_other_authors_cannot_edit_or_see_history_of_someone_elses_post(self):
        other = get_user_model().objects.create_user(
            username="ed-other", email="ed-other@example.test", role="author"
        )
        self.client.force_login(other)

        self.assertEqual(
            self.client.get(
                f"{reverse('blog:post_create')}?edit={self.draft.slug}"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("blog:post_history", kwargs={"slug": self.draft.slug})
            ).status_code,
            404,
        )

    def test_staff_can_edit_live_posts(self):
        Post.objects.filter(pk=self.draft.pk).update(
            status=PostStatus.PUBLISHED, publish_at=timezone.now()
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("blog:post_create"),
            self.form_data(title="Staff retitle", edit=self.draft.slug),
        )

        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, "Staff retitle")

    def test_form_only_offers_public_tags(self):
        public = Tag.objects.create(name="Offered", is_curated=True)
        Tag.objects.create(name="Not offered", ai_suggested=True)
        self.client.force_login(self.author)

        response = self.client.get(reverse("blog:post_create"))

        self.assertContains(response, public.name)
        self.assertNotContains(response, "Not offered")
        self.assertNotContains(response, "w-full")

    def test_new_posts_are_always_drafts(self):
        self.client.force_login(self.author)

        self.client.post(reverse("blog:post_create"), self.form_data())

        created = Post.objects.get(title="A brand new article")
        self.assertEqual(created.status, PostStatus.DRAFT)
        self.assertEqual(created.author, self.author)
        self.assertEqual(created.revisions.count(), 1)


class WorkflowActionTests(EditorialTestCase):
    def test_html_workflow_buttons_request_review_and_publish(self):
        url = reverse("blog:post_workflow", kwargs={"slug": self.draft.slug})
        self.client.force_login(self.author)
        self.client.post(url, {"action": "request_review"})
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, PostStatus.IN_REVIEW)

        denied = self.client.post(url, {"action": "publish"})
        self.assertRedirects(denied, reverse("blog:manage_posts"))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, PostStatus.IN_REVIEW)

        self.client.force_login(self.staff)
        self.client.post(url, {"action": "publish"})
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, PostStatus.PUBLISHED)

    def test_unknown_actions_and_bad_schedule_times_are_rejected(self):
        url = reverse("blog:post_workflow", kwargs={"slug": self.draft.slug})
        self.client.force_login(self.staff)

        self.client.post(url, {"action": "explode"})
        self.client.post(url, {"action": "schedule", "when": "soon"})

        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, PostStatus.DRAFT)

    def test_workflow_requires_post_and_login(self):
        url = reverse("blog:post_workflow", kwargs={"slug": self.draft.slug})
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_scheduled_posts_publish_when_due_and_only_then(self):
        due = Post.objects.create(
            title="Due post",
            body="<p>Scheduled body that is long enough.</p>",
            author=self.author,
            status=PostStatus.SCHEDULED,
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        later = Post.objects.create(
            title="Later post",
            body="<p>Scheduled body that is long enough.</p>",
            author=self.author,
            status=PostStatus.SCHEDULED,
            publish_at=timezone.now() + timedelta(days=1),
        )

        published = publish_due_scheduled_posts()
        second = publish_due_scheduled_posts()

        due.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual((published, second), (1, 0))
        self.assertEqual(due.status, PostStatus.PUBLISHED)
        self.assertEqual(later.status, PostStatus.SCHEDULED)
        self.assertEqual(self.client.get(due.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(later.get_absolute_url()).status_code, 404)


class JsonApiTests(EditorialTestCase):
    def publish(self, post):
        Post.objects.filter(pk=post.pk).update(
            status=PostStatus.PUBLISHED, publish_at=timezone.now()
        )

    def test_public_lookups_never_expose_unpublished_posts(self):
        Post.objects.create(
            title="Secret roadmap draft",
            body="<p>Draft body that is long enough.</p>",
            author=self.author,
            status=PostStatus.IN_REVIEW,
        )
        self.publish(self.draft)

        similar = self.client.get(reverse("blog:api_similar_posts"), {"q": "Secret"})
        listing = self.client.get(reverse("blog:api_posts"))

        self.assertEqual(similar.json()["items"], [])
        self.assertEqual(
            [item["slug"] for item in listing.json()["items"]], [self.draft.slug]
        )
        self.assertNotIn("ed-author@example.test", listing.content.decode())

    def test_related_lookup_requires_a_published_post(self):
        url = reverse("blog:api_related", kwargs={"slug": self.draft.slug})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.publish(self.draft)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_preview_is_sanitised_and_restricted_to_authors(self):
        url = reverse("blog:post_preview")
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url, {"body": "x"}).status_code, 404)

        self.client.force_login(self.author)
        response = self.client.post(url, {"body": "<p>ok</p><script>alert(1)</script>"})

        self.assertTrue(response.json()["ok"])
        self.assertIn("ok", response.json()["body"])
        self.assertNotIn("<script", response.json()["body"])

    def test_autosave_checks_the_post_belongs_to_the_editor(self):
        url = reverse("blog:post_autosave")
        other = get_user_model().objects.create_user(
            username="ed-intruder", email="ed-intruder@example.test", role="author"
        )
        self.client.force_login(other)

        self.assertEqual(
            self.client.post(url, {"post_id": self.draft.pk, "title": "x"}).status_code,
            404,
        )
        self.client.force_login(self.author)
        self.assertEqual(
            self.client.post(url, {"post_id": self.draft.pk, "title": "x"}).status_code,
            200,
        )

    def test_likes_toggle_per_user_and_only_for_live_posts(self):
        url = reverse("blog:post_like", kwargs={"pk": self.draft.pk})
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url).status_code, 404)

        self.publish(self.draft)
        first = self.client.post(url).json()
        second = self.client.post(url).json()

        self.assertEqual((first["action"], first["likes_count"]), ("liked", 1))
        self.assertEqual((second["action"], second["likes_count"]), ("unliked", 0))
        self.assertFalse(PostLike.objects.exists())

    def test_ai_assist_errors_do_not_leak_internals_and_are_rate_limited(self):
        url = reverse("blog:api_ai_assist")
        self.client.force_login(self.author)

        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, {"action": "nope"}).status_code, 400)
        with patch("apps.blog.views_api.allow_action", return_value=False):
            self.assertEqual(
                self.client.post(url, {"action": "outline", "text": "x"}).status_code,
                429,
            )
