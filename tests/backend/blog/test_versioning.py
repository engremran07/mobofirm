from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.blog.forms import PostForm
from apps.blog.models import Post, PostStatus
from apps.blog.services.history import PostHistoryService
from apps.tags.models import Tag


class PostHistoryServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="history-author",
            email="history-author@example.com",
            password="test-password",
        )
        self.post = Post.objects.create(
            title="Original post title",
            summary="Original summary",
            body="<p>Original post body.</p>",
            author=self.user,
            status=PostStatus.DRAFT,
        )
        self.original_tag = Tag.objects.create(
            name="Original topic",
            normalized_name="original topic",
            slug="original-topic",
            is_curated=True,
        )
        self.updated_tag = Tag.objects.create(
            name="Updated topic",
            normalized_name="updated topic",
            slug="updated-topic",
            is_curated=True,
        )
        self.post.tags.add(self.original_tag)
        self.initial_revision = PostHistoryService.record_current_version(
            self.post, user=self.user
        )

    def test_recording_current_version_is_idempotent(self):
        duplicate = PostHistoryService.record_current_version(self.post, user=self.user)

        self.assertEqual(duplicate.pk, self.initial_revision.pk)
        self.assertEqual(self.post.revisions.count(), 1)

    def test_update_records_old_state_and_restore_creates_a_new_version(self):
        PostHistoryService.update_post(
            self.post,
            user=self.user,
            change_summary="Updated the editorial copy",
            title="Updated post title",
            summary="Updated summary",
            body="<p>Updated post body.</p>",
            tags=[self.updated_tag],
        )
        self.post.refresh_from_db()

        self.assertEqual(self.post.version, 2)
        self.assertEqual(self.initial_revision.snapshot["title"], "Original post title")
        self.assertEqual(self.initial_revision.snapshot["tags"], ["original-topic"])

        restored = PostHistoryService.restore_revision(
            self.post, self.initial_revision, user=self.user
        )

        self.assertEqual(restored.title, "Original post title")
        self.assertEqual(restored.summary, "Original summary")
        self.assertEqual(restored.status, PostStatus.DRAFT)
        self.assertEqual(restored.version, 3)
        self.assertEqual(
            list(restored.tags.values_list("slug", flat=True)), ["original-topic"]
        )
        self.assertEqual(
            {revision.snapshot.get("version") for revision in restored.revisions.all()},
            {1, 2},
        )

    def test_unchanged_content_does_not_create_an_extra_revision(self):
        PostHistoryService.update_post(
            self.post,
            user=self.user,
            change_summary="No content change",
            title=self.post.title,
            body=self.post.body,
            tags=[self.original_tag],
        )

        self.post.refresh_from_db()
        self.assertEqual(self.post.version, 1)
        self.assertEqual(self.post.revisions.count(), 1)

    def test_published_posts_cannot_be_restored(self):
        self.post.status = PostStatus.PUBLISHED
        self.post.save()
        original_title = self.post.title

        with self.assertRaises(ValidationError):
            PostHistoryService.restore_revision(
                self.post, self.initial_revision, user=self.user
            )

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.PUBLISHED)
        self.assertEqual(self.post.title, original_title)

    def test_revision_from_another_post_is_rejected(self):
        other_post = Post.objects.create(
            title="Another post title",
            body="<p>Another post body.</p>",
            author=self.user,
            status=PostStatus.DRAFT,
        )

        with self.assertRaises(ValidationError):
            PostHistoryService.restore_revision(
                other_post, self.initial_revision, user=self.user
            )

    def test_editorial_form_does_not_accept_publication_controls(self):
        form = PostForm(
            {
                "title": "Submitted as published",
                "summary": "A valid summary.",
                "body": "<p>A valid body for the editor form.</p>",
                "status": PostStatus.PUBLISHED,
                "publish_at": "2030-01-01T10:00",
                "featured": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("status", form.cleaned_data)
        self.assertNotIn("publish_at", form.cleaned_data)
        self.assertNotIn("featured", form.cleaned_data)


class PostHistoryViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="history-view-author",
            email="history-view-author@example.com",
            password="test-password",
        )
        self.other_user = user_model.objects.create_user(
            username="history-view-other",
            email="history-view-other@example.com",
            password="test-password",
        )
        self.post = Post.objects.create(
            title="History view post",
            body="<p>History view post body.</p>",
            author=self.author,
            status=PostStatus.DRAFT,
        )
        self.revision = PostHistoryService.record_current_version(
            self.post, user=self.author
        )

    def test_history_is_limited_to_the_owner_or_editor(self):
        history_url = reverse("blog:post_history", kwargs={"slug": self.post.slug})
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(history_url).status_code, 404)

        self.client.force_login(self.author)
        response = self.client.get(history_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "History view post")
        self.assertContains(response, "Restore this draft version")

    def test_restore_is_post_only_and_redirects_after_success(self):
        PostHistoryService.update_post(
            self.post,
            user=self.author,
            change_summary="Change before restore",
            title="Changed history view post",
        )
        restore_url = reverse(
            "blog:restore_post_revision",
            kwargs={"slug": self.post.slug, "revision_id": self.revision.pk},
        )
        self.client.force_login(self.author)

        self.assertEqual(self.client.get(restore_url).status_code, 405)
        response = self.client.post(restore_url)

        self.assertRedirects(
            response,
            reverse("blog:post_history", kwargs={"slug": self.post.slug}),
        )
        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "History view post")
