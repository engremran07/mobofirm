from unittest.mock import patch

from django.test import TestCase

from apps.blog.models import Post, PostStatus
from apps.comments.models import Comment
from apps.tags.models import Tag
from apps.users.models import CustomUser


class BlogCommentTagIntegrationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="tag-integration@example.com",
            username="tag-integration",
            password="Complex-test-password-123!",
        )

    @patch("apps.core.ai_client.suggest_tags", return_value=["Django"])
    def test_comment_creation_does_not_change_published_post_tags(
        self, mock_suggest_tags
    ):
        post = Post.objects.create(
            title="Tag integration",
            slug="tag-integration",
            summary="A published post for the comment integration test.",
            body="Django tags are attached when this post is published.",
            author=self.user,
            status=PostStatus.PUBLISHED,
        )
        tag_ids = list(post.tags.values_list("pk", flat=True))
        self.assertEqual(tag_ids, [])
        suggested_tag = Tag.objects.get(normalized_name="django")
        self.assertTrue(suggested_tag.ai_suggested)
        self.assertFalse(suggested_tag.is_curated)

        Comment.objects.create(
            post=post,
            user=self.user,
            body="This comment mentions unrelated terms.",
            status=Comment.Status.APPROVED,
            is_approved=True,
        )

        post.refresh_from_db()
        self.assertEqual(list(post.tags.values_list("pk", flat=True)), tag_ids)
        mock_suggest_tags.assert_called_once()
