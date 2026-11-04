from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tags.services.tagging_service import TaggingService

User = get_user_model()


class AnnotationConflictTest(TestCase):
    """Ensure annotate doesn't clash with Tag.usage_count field"""

    def setUp(self):
        self.service = TaggingService()
        self.user = User.objects.create_user(  # type: ignore[attr-defined]
            email="a@a.com", username="a", password="pw"
        )

    def test_get_popular_tags_updates_usage_count_and_no_annotation_conflict(self):
        # Create objects and tag them
        from apps.blog.models import Post

        p1 = Post.objects.create(title="A1", slug="a1", body="x", author=self.user)
        p2 = Post.objects.create(title="A2", slug="a2", body="x", author=self.user)

        self.service.tag_object(p1, "alpha")
        self.service.tag_object(p2, "alpha")

        popular = self.service.get_popular_tags(limit=5, model_class=Post)

        # Should return the tag and usage_count must reflect number of objects
        assert popular.exists()
        t = popular.first()
        assert t is not None
        assert t.name == "alpha"
        assert t.usage_count == 2
