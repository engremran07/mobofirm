from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.blog.models import PostRevision, PostStatus
from apps.firmwares.guide_service import generate_guide_for_brand_model
from apps.firmwares.models import (
    Brand,
    FlashingGuideTemplate,
    GeneratedFlashingGuide,
    Model,
)
from apps.governance.models import ReviewCase


class FlashingGuideGovernanceTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username="guide-author",
            email="guide-author@example.com",
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

    def test_auto_submit_creates_a_draft_under_governance_and_is_idempotent(self):
        guide = generate_guide_for_brand_model(
            self.template,
            self.brand,
            self.model,
        )

        self.assertIsNotNone(guide)
        assert guide is not None
        post = guide.post
        self.assertEqual(post.author, self.author)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        self.assertEqual(PostRevision.objects.filter(post=post).count(), 1)
        case = ReviewCase.objects.get(object_id=str(post.pk))
        self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)

        duplicate = generate_guide_for_brand_model(
            self.template,
            self.brand,
            self.model,
        )

        self.assertIsNone(duplicate)
        self.assertEqual(GeneratedFlashingGuide.objects.count(), 1)

    def test_generation_fails_explicitly_without_a_staff_author(self):
        self.author.delete()

        with self.assertRaisesMessage(
            ValueError, "A staff author is required to generate a governed guide."
        ):
            generate_guide_for_brand_model(
                self.template,
                self.brand,
                self.model,
            )

        self.assertEqual(GeneratedFlashingGuide.objects.count(), 0)
