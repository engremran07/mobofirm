from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tags.models import Tag
from apps.tags.models_tagged_item import TaggedItem
from apps.tags.services.tagging_service import TaggingService

User = get_user_model()


class TaggingRegressions(TestCase):
    def setUp(self):
        self.service = TaggingService()
        self.user = User.objects.create_user(  # type: ignore[attr-defined]
            email="r@r.com", username="r", password="pw"
        )

    def test_content_object_filtering_on_taggeditem(self):
        # Ensure TaggedItem can be filtered by content_type and object_id
        # Note: GenericForeignKey cannot be used directly in filter(),
        # use content_type and object_id instead
        from django.contrib.contenttypes.models import ContentType

        from apps.blog.models import Post

        post = Post.objects.create(title="R", slug="r", body="x", author=self.user)
        self.service.tag_object(post, "regress")

        # Correct way to filter by content object
        content_type = ContentType.objects.get_for_model(post)
        assert (
            TaggedItem.objects.filter(
                content_type=content_type, object_id=post.pk
            ).count()
            == 1
        )

    def test_tag_slug_collision_race(self):
        # simulate slug collision by creating Tag directly and via service
        t = Tag.objects.create(name="race-exist", slug="race-exist")
        # now call service to create the same name; should return existing not error
        ti = self.service.tag_object(t, "race-exist", created_by=self.user)
        assert Tag.objects.filter(name="race-exist").exists()
        assert ti is not None
