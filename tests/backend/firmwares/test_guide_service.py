from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.blog.models import PostRevision, PostStatus
from apps.firmwares.blog_automation import get_automation_author
from apps.firmwares.guide_service import generate_guide_for_brand_model
from apps.firmwares.models import (
    Brand,
    FlashingGuideTemplate,
    GeneratedFlashingGuide,
    Model,
)


class FlashingGuideReviewTests(TestCase):
    def setUp(self):
        self.author = get_automation_author()
        get_user_model().objects.create_user(
            username="guide-editor",
            email="guide-editor@example.com",
            password="test-password",
            is_staff=True,
        )
        with (
            patch(
                "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_brand",
                return_value={},
            ),
            patch(
                "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_model",
                return_value={},
            ),
        ):
            self.brand = Brand.objects.create(name="Guide Brand", slug="guide-brand")
            self.model = Model.objects.create(
                brand=self.brand,
                name="Guide Phone",
                slug="guide-phone",
            )
        self.template = FlashingGuideTemplate.objects.create(
            title_template="Flash {brand} {model}",
            body_template="A practical guide for {brand} {model}. {steps}",
            summary_template="Guide for {brand} {model}",
            guide_type=FlashingGuideTemplate.GuideType.GENERAL,
            brand=self.brand,
            submit_for_review=True,
        )

    def test_auto_submit_creates_a_draft_for_review_and_is_idempotent(self):
        guide = generate_guide_for_brand_model(
            self.template,
            self.brand,
            self.model,
        )

        self.assertIsNotNone(guide)
        assert guide is not None
        post = guide.post
        self.assertEqual(post.author, self.author)
        self.assertFalse(post.author.is_staff)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        self.assertEqual(PostRevision.objects.filter(post=post).count(), 1)

        duplicate = generate_guide_for_brand_model(
            self.template,
            self.brand,
            self.model,
        )

        self.assertIsNone(duplicate)
        self.assertEqual(GeneratedFlashingGuide.objects.count(), 1)

    def test_brand_wide_guides_are_unique_per_template_and_brand(self):
        first = generate_guide_for_brand_model(self.template, self.brand, None)
        second = generate_guide_for_brand_model(self.template, self.brand, None)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(GeneratedFlashingGuide.objects.count(), 1)
