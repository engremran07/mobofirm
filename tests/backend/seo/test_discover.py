from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from PIL import Image

from apps.blog.models import Post
from apps.seo import scan
from apps.seo.imagefacts import local_image_width
from apps.seo.models import Issue, Metadata, SEOModel
from tests.backend.seo.seo_helpers import SITE, make_catalog

HOSTS = ["example.org", "testserver", "localhost"]


def codes(path: str) -> set[str]:
    return set(
        Issue.objects.filter(url=path, status=Issue.Status.OPEN).values_list(
            "code", flat=True
        )
    )


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class DiscoverReadinessTests(TestCase):
    def setUp(self):
        self.data = make_catalog()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.path = self.data.post.get_absolute_url()

    def image(self, width: int) -> str:
        target = Path(self.media) / "posts" / f"lead-{width}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, max(1, width // 2)), "navy").save(target)
        return f"/media/posts/lead-{width}.png"

    def test_a_post_without_a_lead_image_is_not_discover_ready(self):
        scan.run_scan()

        self.assertIn("discover_no_image", codes(self.path))
        issue = Issue.objects.get(url=self.path, code="discover_no_image")
        self.assertEqual(issue.seo_field, "social_image")

    def test_a_narrow_lead_image_is_flagged_and_a_wide_one_is_not(self):
        Post.objects.filter(pk=self.data.post.pk).update(hero_image=self.image(800))
        scan.run_scan()
        self.assertIn("discover_image_small", codes(self.path))
        self.assertNotIn("discover_no_image", codes(self.path))

        Post.objects.filter(pk=self.data.post.pk).update(hero_image=self.image(1280))
        scan.run_scan()

        self.assertNotIn("discover_image_small", codes(self.path))
        self.assertNotIn("discover_no_image", codes(self.path))

    def test_a_robots_override_without_large_previews_is_flagged_with_a_proposal(self):
        container = SEOModel.objects.create(
            content_type=ContentType.objects.get(app_label="blog", model="post"),
            object_id=str(self.data.post.pk),
        )
        Metadata.objects.create(seo=container, robots_directives="index, follow")
        scan.run_scan()

        issue = Issue.objects.get(url=self.path, code="discover_max_image_preview")

        self.assertIn("max-image-preview:large", issue.suggestion["value"])

    def test_dates_author_and_article_schema_are_present_on_a_normal_post(self):
        scan.run_scan()

        found = codes(self.path)

        self.assertFalse(
            found & {"discover_article_schema", "discover_dates", "discover_author"}
        )
        self.assertNotIn("discover_max_image_preview", found)

    def test_only_articles_get_discover_checks(self):
        scan.run_scan()

        self.assertFalse(
            Issue.objects.filter(code__startswith="discover_")
            .exclude(url=self.path)
            .exists()
        )

    def test_image_width_is_read_only_from_local_storage(self):
        self.assertEqual(local_image_width(self.image(640)), 640)
        self.assertEqual(local_image_width(f"{SITE}{self.image(640)}"), 640)
        self.assertIsNone(local_image_width("https://cdn.elsewhere.example/a.png"))
        self.assertIsNone(local_image_width("/media/missing.png"))
        self.assertIsNone(local_image_width("/other/area.png"))
