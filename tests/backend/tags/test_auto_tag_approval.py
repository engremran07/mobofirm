from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.governance.models import ReviewCase
from apps.tags.models import Tag
from apps.tags.services.heuristics import auto_tag_post


class AutomatedTagApprovalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tag-author",
            email="tag-author@example.com",
            password="test-password",
        )
        self.post = Post.objects.create(
            title="Automatic tag test",
            body="<p>Content for automatic tag governance.</p>",
            author=self.user,
            status=PostStatus.DRAFT,
        )

    @patch(
        "apps.tags.services.heuristics.suggest_tags_from_text",
        return_value=[{"name": "Unreviewed topic", "normalized": "unreviewed topic"}],
    )
    def test_unreviewed_tag_is_submitted_but_not_attached(self, _suggest):
        attached = auto_tag_post(self.post, allow_create=True)
        tag = Tag.objects.get(slug="unreviewed-topic")

        self.assertEqual(attached, [])
        self.assertFalse(self.post.tags.exists())
        self.assertFalse(tag.is_curated)
        case = ReviewCase.objects.get(object_id=str(tag.pk))
        self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)

    @patch(
        "apps.tags.services.heuristics.suggest_tags_from_text",
        return_value=[{"name": "Reviewed topic", "normalized": "reviewed topic"}],
    )
    def test_curated_tag_is_attached(self, _suggest):
        tag = Tag.objects.create(
            name="Reviewed topic",
            normalized_name="reviewed topic",
            slug="reviewed-topic",
            is_curated=True,
        )

        attached = auto_tag_post(self.post, allow_create=True)

        self.assertEqual([item.pk for item in attached], [tag.pk])
        self.assertEqual(list(self.post.tags.all()), [tag])

    def test_public_tag_surfaces_hide_unreviewed_suggestions(self):
        tag = Tag.objects.create(
            name="Pending public tag",
            normalized_name="pending public tag",
            slug="pending-public-tag",
            ai_suggested=True,
            is_curated=False,
        )

        search = self.client.get(reverse("tags:search"), {"q": tag.name})
        listing = self.client.get(reverse("tags:list"))
        detail = self.client.get(reverse("tags:detail", kwargs={"slug": tag.slug}))

        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["items"], [])
        self.assertNotContains(listing, tag.name)
        self.assertEqual(detail.status_code, 404)

    def test_published_blog_pages_render_only_curated_tags(self):
        pending = Tag.objects.create(
            name="Pending article tag",
            normalized_name="pending article tag",
            slug="pending-article-tag",
            ai_suggested=True,
            is_curated=False,
        )
        curated = Tag.objects.create(
            name="Approved article tag",
            normalized_name="approved article tag",
            slug="approved-article-tag",
            is_curated=True,
        )
        post = Post.objects.create(
            title="Public tag visibility",
            body="<p>Public article content for tag visibility.</p>",
            author=self.user,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
        )
        post.tags.add(pending, curated)

        detail = self.client.get(
            reverse("blog:post_detail", kwargs={"slug": post.slug})
        )
        listing = self.client.get(reverse("blog:post_list"))

        self.assertContains(detail, curated.name)
        self.assertNotContains(detail, pending.name)
        self.assertContains(listing, curated.name)
        self.assertNotContains(listing, pending.name)
