from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.blog.models import Post, PostStatus
from apps.seo.models import Metadata, SEOModel
from apps.users.models import CustomUser as User


class TestIntegrationAutoTagSEO(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(  # type: ignore[attr-defined]
            email="i@i.com", username="i", password="pw"
        )

    def test_publish_triggers_auto_tag_and_seo(self):
        post = Post.objects.create(
            title="Django SEO Test",
            slug="django-seo-test",
            summary="Sample summary for SEO generation",
            body="Django SEO SEO SEO test auto tagging django django django",
            author=self.user,
            status=PostStatus.PUBLISHED,
        )

        # Post-save signal should have run apply_post_seo
        post.refresh_from_db()

        # Tags should have been attached (SEO.auto_tags default True)
        tags = post.tags.all()
        assert tags.count() > 0, (
            "Expected auto-generated tags to be attached to the post"
        )

        # SEOModel and Metadata must exist
        ct = ContentType.objects.get_for_model(post.__class__)
        seo_exists = SEOModel.objects.filter(
            content_type=ct, object_id=post.pk
        ).exists()
        assert seo_exists, "Expected SEOModel to be created for the post"

        seo = SEOModel.objects.get(content_type=ct, object_id=post.pk)
        assert hasattr(seo, "metadata"), "Expected Metadata for SEOModel"
        meta: Metadata = seo.metadata  # type: ignore[attr-defined]
        assert meta.meta_title, "Expected meta_title to be generated"
        assert meta.meta_description, "Expected meta_description to be generated"
