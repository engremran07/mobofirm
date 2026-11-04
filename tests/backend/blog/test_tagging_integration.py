from unittest.mock import patch

from django.test import TestCase

from apps.blog.models import Post, PostStatus
from apps.governance.models import ReviewCase
from apps.tags.models import Tag
from apps.users.models import CustomUser


class BlogPostTagIntegrationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="integration@example.com",
            username="integration",
        )

    @patch("apps.core.ai_client.suggest_tags", return_value=["Django"])
    def test_publishing_post_submits_unreviewed_tag_without_attaching_it(
        self, mock_suggest_tags
    ):
        post = Post.objects.create(
            title="Django Testing",
            slug="django-testing",
            summary="Testing a Django project with regression coverage.",
            body="Django testing helps teams create reliable applications.",
            author=self.user,
            status=PostStatus.PUBLISHED,
        )

        post.refresh_from_db()
        suggested_tag = Tag.objects.get(name="Django", ai_suggested=True)
        self.assertFalse(post.tags.exists())
        self.assertFalse(suggested_tag.is_curated)
        case = ReviewCase.objects.get(object_id=str(suggested_tag.pk))
        self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)
        mock_suggest_tags.assert_called_once()
