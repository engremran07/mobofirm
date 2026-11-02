from __future__ import annotations

import os
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client, TestCase, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.blog.models import BlogSettings, Post, PostStatus  # noqa: E402
from apps.site_settings.models import SiteSettings  # noqa: E402

from .models import Comment  # noqa: E402

User = get_user_model()


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    ROOT_URLCONF="app.urls",
    SECURE_SSL_REDIRECT=False,
)
class CommentModerationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Patch the AI behavior service at class level to avoid JSON serialization issues
        cls.ai_patcher = patch(
            "apps.devices.services.record_insight", return_value=None
        )
        cls.ai_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.ai_patcher.stop()
        super().tearDownClass()

    def setUp(self) -> None:
        # Ensure SiteSettings exists
        SiteSettings.get_solo()
        # Use BlogSettings for blog-specific settings
        blog_settings = BlogSettings.get_solo()
        blog_settings.enable_blog = True
        blog_settings.enable_blog_comments = True
        blog_settings.save()
        self.user = User.objects.create_user(email="u@example.com", password="pass")  # type: ignore[attr-defined]
        self.client = Client()
        self.client.force_login(self.user)
        self.post = Post.objects.create(
            title="Hello",
            body="Body",
            author=self.user,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
        )

    @patch("apps.consent.decorators.consent_check", return_value=True)
    @patch("apps.comments.views._has_comments_consent", return_value=True)
    @patch("apps.comments.views.classify_comment")
    def test_add_comment_marks_spam_on_high_toxicity(
        self, mock_classify, mock_consent, mock_decorator_consent
    ):
        # Mock the moderation result with high toxicity
        from types import SimpleNamespace

        mock_classify.return_value = SimpleNamespace(
            label="spam",
            score=0.9,
            rationale="High toxicity detected",
            toxicity_score=0.9,
            spam_score=0.9,
            hate_score=0.0,
        )
        url = reverse("comments:add_comment_json", kwargs={"slug": self.post.slug})
        res = self.client.post(url, {"body": "bad words"})
        assert res.status_code == 200
        payload = res.json()
        assert payload["status"] == Comment.Status.SPAM
        comment = Comment.objects.get(pk=payload["id"])
        assert comment.status == Comment.Status.SPAM
        assert not comment.is_approved

    @patch("apps.consent.decorators.consent_check", return_value=True)
    @patch("apps.comments.views._has_comments_consent", return_value=True)
    def test_list_comments_excludes_non_approved(
        self, mock_consent, mock_decorator_consent
    ):
        approved = Comment.objects.create(
            post=self.post,
            user=self.user,
            body="ok",
            status=Comment.Status.APPROVED,
            is_approved=True,
        )
        Comment.objects.create(
            post=self.post,
            user=self.user,
            body="nope",
            status=Comment.Status.SPAM,
            is_approved=False,
        )
        url = reverse("comments:list_comments", kwargs={"slug": self.post.slug})
        res = self.client.get(url)
        assert res.status_code == 200
        ids = [c["id"] for c in res.json()["items"]]
        assert approved.pk in ids
        assert len(ids) == 1

    def test_moderation_actions(self):
        staff = User.objects.create_user(  # type: ignore[attr-defined]
            email="staff@example.com", password="pass", is_staff=True
        )
        self.client.force_login(staff)
        comment = Comment.objects.create(
            post=self.post,
            user=self.user,
            body="pending",
            status=Comment.Status.PENDING,
            is_approved=False,
        )
        url = reverse("comments:moderation_action")
        res = self.client.post(url, {"comment_id": comment.pk, "action": "approve"})
        assert res.status_code == 302
        comment.refresh_from_db()
        assert comment.status == Comment.Status.APPROVED
        assert comment.is_approved
