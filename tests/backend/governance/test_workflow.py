import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.blog.views import post_detail
from apps.comments.models import Comment
from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.models import (
    Brand,
    FirmwarePackageFormat,
    Model,
    OfficialFirmware,
    PendingFirmware,
)
from apps.firmwares.public_views import get_all_firmwares_for_model
from apps.governance.models import ReviewCase, ReviewEvent
from apps.governance.services import (
    decide_case,
    fulfill_case,
    report_issue,
    start_review,
    submit_for_review,
)
from apps.tags.models import Tag

User = get_user_model()


class GovernanceWorkflowTests(TestCase):
    def setUp(self):
        self.submitter = User.objects.create_user(
            username="submitter",
            email="submitter@example.test",
            password="test-password",
        )
        self.reviewer = User.objects.create_user(
            username="reviewer",
            email="reviewer@example.test",
            password="test-password",
        )
        self.publisher = User.objects.create_user(
            username="publisher",
            email="publisher@example.test",
            password="test-password",
        )
        review_permission = Permission.objects.get(codename="can_review_content")
        publish_permission = Permission.objects.get(codename="can_publish_content")
        self.reviewer.user_permissions.add(review_permission)
        self.publisher.user_permissions.add(publish_permission)

    def make_post(self):
        return Post.objects.create(
            title="Governance example",
            body="<p>A sufficiently long example body for a governed post.</p>",
            author=self.submitter,
            status=PostStatus.DRAFT,
        )

    def approve(self, case):
        start_review(case.pk, self.reviewer, rationale="Independent review started.")
        return decide_case(
            case.pk,
            self.reviewer,
            "approve",
            "Content and policy checks passed.",
        )

    def test_post_requires_distinct_review_and_publishers(self):
        post = self.make_post()
        case = submit_for_review(post, actor=self.submitter)
        duplicate = submit_for_review(post, actor=self.submitter)

        self.assertEqual(duplicate.pk, case.pk)
        self.assertEqual(case.events.count(), 1)
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)

        with self.assertRaises(PermissionDenied):
            start_review(case.pk, self.submitter)

        self.approve(case)
        with self.assertRaises(PermissionDenied):
            fulfill_case(case.pk, self.reviewer)
        completed = fulfill_case(case.pk, self.publisher)

        post.refresh_from_db()
        self.assertEqual(completed.status, ReviewCase.Status.COMPLETED)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertTrue(post.is_published)
        self.assertEqual(
            list(case.events.values_list("action", flat=True)),
            [
                ReviewEvent.Action.SUBMITTED,
                ReviewEvent.Action.REVIEW_STARTED,
                ReviewEvent.Action.APPROVED,
                ReviewEvent.Action.FULFILLED,
            ],
        )

    def test_repeated_fulfillment_is_idempotent(self):
        post = self.make_post()
        case = submit_for_review(post, actor=self.submitter)
        self.approve(case)

        result = fulfill_case(
            case.pk,
            self.publisher,
            idempotency_key="37ce7ac4-1e07-4553-a52b-093b72064ac1",
        )
        repeated = fulfill_case(
            case.pk,
            self.publisher,
            idempotency_key="37ce7ac4-1e07-4553-a52b-093b72064ac1",
        )

        self.assertEqual(result.pk, repeated.pk)
        self.assertEqual(
            case.events.filter(action=ReviewEvent.Action.FULFILLED).count(), 1
        )

    def test_corrective_action_unpublishes_and_reopens_audit_case(self):
        post = self.make_post()
        case = submit_for_review(post, actor=self.submitter)
        self.approve(case)
        fulfill_case(case.pk, self.publisher)

        report_issue(case.pk, self.reviewer, "The published content needs correction.")

        post.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        self.assertEqual(case.status, ReviewCase.Status.CORRECTION_REQUIRED)
        self.assertTrue(case.is_open)
        self.assertEqual(case.events.count(), 5)

        resubmitted = submit_for_review(post, actor=self.submitter)
        self.assertEqual(resubmitted.pk, case.pk)
        self.assertEqual(resubmitted.revision, 2)
        self.assertEqual(resubmitted.status, ReviewCase.Status.SUBMITTED)

    def test_comment_is_not_visible_until_governance_fulfillment(self):
        post = self.make_post()
        comment = Comment.objects.create(
            post=post,
            user=self.submitter,
            body="A comment awaiting review.",
            status=Comment.Status.PENDING,
            is_approved=False,
        )
        case = submit_for_review(comment, actor=self.submitter)
        self.approve(case)

        comment.refresh_from_db()
        self.assertFalse(comment.is_approved)
        fulfill_case(case.pk, self.publisher)

        comment.refresh_from_db()
        self.assertTrue(comment.is_approved)
        self.assertEqual(comment.status, Comment.Status.APPROVED)

    def test_ai_suggested_tag_is_submitted_for_review(self):
        tag = Tag.objects.create(name="AI curation", ai_suggested=True)

        case = ReviewCase.objects.get(object_id=str(tag.pk))
        self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)
        self.assertIsNone(case.requested_by)

        self.approve(case)
        fulfill_case(case.pk, self.publisher)
        tag.refresh_from_db()
        self.assertTrue(tag.is_curated)

    def test_firmware_is_routed_only_by_separate_authorized_publisher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir)
            staged = storage_root / "pending" / "firmware.zip"
            staged.parent.mkdir()
            staged.write_bytes(b"test firmware")
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
                brand = Brand.objects.create(name="Example", slug="example")
                model = Model.objects.create(brand=brand, name="Phone", slug="phone")
            post = Post.objects.create(
                title="Example Phone firmware",
                slug="example-phone-firmware",
                body="<p>Approved editorial content remains unchanged.</p>",
                author=self.submitter,
                status=PostStatus.PUBLISHED,
                firmware_brand=brand,
                firmware_model=model,
                is_ai_generated=True,
            )
            original_body = post.body
            pending = PendingFirmware.objects.create(
                original_file_name="firmware.zip",
                stored_file_path=str(staged),
                uploader=self.submitter,
                uploaded_brand=brand,
                uploaded_model=model,
            )
            case = ReviewCase.objects.get(object_id=str(pending.pk))

            self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)
            start_review(case.pk, self.reviewer, rationale="Upload inspected.")
            with self.assertRaisesMessage(
                ValidationError, "Unsupported firmware package format."
            ):
                decide_case(
                    case.pk,
                    self.reviewer,
                    "approve",
                    "Invalid package format rejected.",
                    decision_data={
                        "category": "official",
                        "brand_id": brand.pk,
                        "model_id": model.pk,
                        "package_format": "unsupported",
                    },
                )
            decide_case(
                case.pk,
                self.reviewer,
                "approve",
                "Firmware metadata and file are acceptable.",
                decision_data={
                    "category": "official",
                    "brand_id": brand.pk,
                    "model_id": model.pk,
                    "package_format": FirmwarePackageFormat.FASTBOOT_ROM,
                },
            )
            with self.captureOnCommitCallbacks(execute=True):
                with override_settings(FIRMWARE_STORAGE_ROOT=storage_root):
                    fulfill_case(case.pk, self.publisher)

            routed = OfficialFirmware.objects.get(original_file_name="firmware.zip")
            self.assertEqual(
                routed.package_format,
                FirmwarePackageFormat.FASTBOOT_ROM,
            )
            unreviewed = OfficialFirmware.objects.create(
                original_file_name="unreviewed.zip",
                stored_file_path="unreviewed.zip",
                uploader=self.submitter,
                brand=brand,
                model=model,
            )
            self.assertTrue(Path(routed.stored_file_path).is_file())
            self.assertFalse(staged.exists())
            case.refresh_from_db()
            self.assertEqual(case.status, ReviewCase.Status.COMPLETED)
            self.assertEqual(case.result_object_id, str(routed.pk))
            post.refresh_from_db()
            self.assertEqual(post.status, PostStatus.PUBLISHED)
            self.assertEqual(post.body, original_body)
            self.assertNotIn(
                unreviewed.pk,
                [firmware.pk for firmware in get_all_firmwares_for_model(model)],
            )

            request = RequestFactory().get(f"/blog/{post.slug}/")
            request.user = post.author
            blog_api = SimpleNamespace(
                get_settings=lambda: {"enable_blog": True},
            )
            with (
                patch("apps.blog.views.AppService.get", return_value=blog_api),
                patch("apps.blog.views._get_site_settings_snapshot", return_value={}),
                patch("apps.blog.views.render_to_string", return_value=""),
                patch(
                    "apps.blog.views.render", return_value=HttpResponse("ok")
                ) as render,
            ):
                response = post_detail(request, post.slug)

            self.assertEqual(response.status_code, 200)
            context = render.call_args.args[2]
            self.assertEqual(
                [firmware.pk for firmware in context["firmware_table_data"]],
                [routed.pk],
            )

    def test_generated_firmware_posts_enter_governance_review(self):
        author = User.objects.create_user(
            username="firmware-author",
            email="firmware-author@example.test",
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
            brand = Brand.objects.create(name="ReviewBrand", slug="review-brand")
            model = Model.objects.create(
                brand=brand, name="ReviewPhone", slug="review-phone"
            )

        post = FirmwareBlogService.generate_firmware_post(model, force_update=True)

        self.assertIsNotNone(post)
        assert post is not None
        self.assertEqual(post.author, author)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        suggested_tag = Tag.objects.get(
            slug="reviewbrand", ai_suggested=True, is_curated=False
        )
        self.assertFalse(post.tags.filter(pk=suggested_tag.pk).exists())
        self.assertTrue(
            ReviewCase.objects.filter(object_id=str(suggested_tag.pk)).exists()
        )
        case = ReviewCase.objects.get(
            content_type=ContentType.objects.get_for_model(post),
            object_id=str(post.pk),
        )
        self.assertEqual(case.status, ReviewCase.Status.SUBMITTED)

    def test_governance_queue_requires_a_governance_permission(self):
        user = User.objects.create_user(
            username="reader",
            email="reader@example.test",
            password="test-password",
        )
        client = Client()
        client.force_login(user)

        response = client.get(reverse("governance:case-list"))

        self.assertEqual(response.status_code, 403)
