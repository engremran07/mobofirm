from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import BlogSettings, Post, PostStatus
from apps.comments.models import (
    Comment,
    CommentReport,
    CommentSettings,
    CommentVote,
)
from apps.comments.services import assess_comment
from apps.comments.tasks import notify_comment_published
from apps.users.models import Notification


class CommentTestCase(TestCase):
    def setUp(self):
        cache.clear()
        BlogSettings.objects.update_or_create(
            pk=1, defaults={"enable_blog": True, "enable_blog_comments": True}
        )
        CommentSettings.objects.update_or_create(
            pk=1, defaults={"enable_comments": True}
        )
        user_model = get_user_model()
        self.author = user_model.objects.create_user(
            username="cm-post-author", email="cm-post-author@example.test"
        )
        self.reader = user_model.objects.create_user(
            username="cm-reader",
            email="cm-reader@example.test",
            full_name="Riley Reader",
        )
        self.other = user_model.objects.create_user(
            username="cm-other", email="cm-other@example.test"
        )
        self.post = Post.objects.create(
            title="Commented post",
            body="<p>A post people can comment on.</p>",
            author=self.author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now() - timedelta(minutes=1),
        )
        self.add_url = reverse("comments:add_comment", kwargs={"slug": self.post.slug})
        self.client.force_login(self.reader)

    def comment(self, body="A thoughtful comment.", user=None, **extra):
        values = {
            "post": self.post,
            "user": user or self.reader,
            "body": body,
            "status": Comment.Status.APPROVED,
        }
        values.update(extra)
        return Comment.objects.create(**values)


class ModerationRuleTests(TestCase):
    def test_clean_comments_are_approved_automatically(self):
        result = assess_comment("Thanks, this fixed my flashing problem!")

        self.assertEqual((result.label, result.score), ("approved", 0.0))

    def test_signals_queue_or_hide_a_comment(self):
        cases = {
            "pending": [
                "Look at https://a.example https://b.example https://c.example please",
                "WHY IS THIS NOT WORKING FOR ME I HAVE TRIED EVERYTHING",
                "You are an idiot",
            ],
            "spam": ["buy now click here for free money https://x.example"],
        }
        for label, bodies in cases.items():
            for body in bodies:
                with self.subTest(body=body):
                    self.assertEqual(assess_comment(body).label, label)

    def test_duplicates_empty_and_repeated_characters(self):
        self.assertEqual(assess_comment("same text").label, "approved")
        self.assertEqual(
            assess_comment("same text", duplicate_of_recent=True).label, "pending"
        )
        self.assertEqual(assess_comment("   ").label, "rejected")
        self.assertEqual(assess_comment("aaaaaaaaaaaaaaa").label, "pending")

    def test_the_link_allowance_is_configurable(self):
        body = "See https://a.example and https://b.example"

        self.assertEqual(assess_comment(body, max_links=0).label, "pending")
        self.assertEqual(assess_comment(body, max_links=2).label, "approved")


class SubmissionTests(CommentTestCase):
    def test_clean_comment_is_published_immediately_without_consent_banner(self):
        response = self.client.post(self.add_url, {"body": "Great guide, thanks!"})

        comment = Comment.objects.get()
        self.assertRedirects(
            response,
            f"{self.post.get_absolute_url()}#comment-{comment.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(comment.status, Comment.Status.APPROVED)
        self.assertTrue(comment.is_approved)
        self.assertEqual(comment.moderation_flags["moderation"]["label"], "approved")
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 1)

    def test_suspicious_comment_waits_for_review_and_is_hidden_from_others(self):
        self.client.post(
            self.add_url,
            {"body": "Check https://a.example https://b.example https://c.example"},
        )

        comment = Comment.objects.get()
        self.assertEqual(comment.status, Comment.Status.PENDING)
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 0)
        own = self.client.get(self.post.get_absolute_url())
        self.assertContains(own, "Awaiting review")
        self.client.force_login(self.other)
        self.assertNotContains(
            self.client.get(self.post.get_absolute_url()), "a.example"
        )

    def test_spam_is_hidden_and_marked(self):
        self.client.post(
            self.add_url, {"body": "buy now click here free money http://x.example"}
        )

        self.assertEqual(Comment.objects.get().status, Comment.Status.SPAM)

    def test_honeypot_rejects_without_creating_a_comment(self):
        response = self.client.post(self.add_url, {"body": "Hello there", "hp": "bot"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Comment.objects.exists())

    def test_json_endpoint_reports_status_and_validation_errors(self):
        url = reverse("comments:add_comment_json", kwargs={"slug": self.post.slug})

        ok = self.client.post(url, {"body": "Looks good to me"})
        short = self.client.post(url, {"body": "ab"})
        long = self.client.post(url, {"body": "x" * 5001})
        honeypot = self.client.post(url, {"body": "Hello", "hp": "x"})

        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["status"], "approved")
        self.assertNotIn("cm-reader@example.test", ok.content.decode())
        self.assertEqual(ok.json()["user"], "Riley Reader")
        for response in (short, long, honeypot):
            self.assertEqual(response.status_code, 400)
            self.assertFalse(response.json()["ok"])

    def test_rate_limit_applies_per_minute(self):
        statuses = []
        for index in range(7):
            response = self.client.post(
                reverse("comments:add_comment_json", kwargs={"slug": self.post.slug}),
                {"body": f"Distinct comment number {index}"},
            )
            statuses.append(response.status_code)

        self.assertEqual(statuses[:5], [200] * 5)
        self.assertEqual(statuses[5:], [429, 429])

    def test_anonymous_users_are_sent_to_sign_in(self):
        self.client.logout()

        response = self.client.post(self.add_url, {"body": "Hello there"})

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.assertFalse(Comment.objects.exists())

    def test_comments_respect_every_off_switch(self):
        for model, field in (
            (CommentSettings, "enable_comments"),
            (BlogSettings, "enable_blog_comments"),
        ):
            model.objects.filter(pk=1).update(**{field: False})
            response = self.client.post(
                reverse("comments:add_comment_json", kwargs={"slug": self.post.slug}),
                {"body": "Hello there"},
            )
            self.assertEqual(response.status_code, 403, field)
            model.objects.filter(pk=1).update(**{field: True})
        Post.objects.filter(pk=self.post.pk).update(allow_comments=False)
        closed = self.client.post(self.add_url, {"body": "Hello there"})
        self.assertEqual(closed.status_code, 302)
        self.assertFalse(Comment.objects.exists())

    def test_unpublished_posts_do_not_accept_comments_from_strangers(self):
        Post.objects.filter(pk=self.post.pk).update(status=PostStatus.DRAFT)

        self.client.post(self.add_url, {"body": "Hello there"})

        self.assertFalse(Comment.objects.exists())

    def test_replies_are_threaded_and_only_to_published_parents(self):
        parent = self.comment("Parent comment text")
        pending = self.comment("Pending parent", status=Comment.Status.PENDING)

        ok = self.client.post(
            self.add_url, {"body": "A reply to you", "parent": parent.pk}
        )
        bad = self.client.post(
            self.add_url, {"body": "Reply to pending", "parent": pending.pk}
        )
        foreign = self.client.post(
            self.add_url, {"body": "Reply nowhere", "parent": 99999}
        )

        self.assertEqual(ok.status_code, 302)
        reply = Comment.objects.get(body="<p>A reply to you</p>")
        self.assertEqual(reply.parent, parent)
        self.assertFalse(Comment.objects.filter(body__contains="Reply to pending").exists())
        self.assertFalse(Comment.objects.filter(body__contains="Reply nowhere").exists())
        page = self.client.get(self.post.get_absolute_url())
        self.assertContains(page, "comment-list--children")
        self.assertContains(page, "A reply to you")
        self.assertEqual(bad.status_code, 302)
        self.assertEqual(foreign.status_code, 302)

    def test_comment_form_markup_is_accessible(self):
        page = self.client.get(self.post.get_absolute_url())

        self.assertContains(page, 'for="comment-form-body"')
        self.assertContains(page, "inert")
        self.assertNotContains(page, 'aria-hidden="true"><label for="comment-hp"')

    def test_comment_bodies_are_escaped(self):
        self.comment("<script>alert(1)</script> and <b>bold</b>")

        page = self.client.get(self.post.get_absolute_url())

        self.assertNotContains(page, "<script>alert(1)")
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_commenters_are_shown_by_public_name_never_email(self):
        self.comment("Visible comment text")

        page = self.client.get(self.post.get_absolute_url())

        self.assertContains(page, "Riley Reader")
        self.assertNotContains(page, "cm-reader@example.test")

    def test_api_listing_returns_only_published_nested_comments(self):
        parent = self.comment("Visible parent comment")
        child = self.comment("Visible child comment", parent=parent)
        self.comment("Spam comment", status=Comment.Status.SPAM)
        url = reverse("comments:list_comments", kwargs={"slug": self.post.slug})

        data = self.client.get(url).json()

        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["children"][0]["id"], child.pk)
        self.assertNotIn("cm-reader@example.test", str(data))


class InteractionTests(CommentTestCase):
    def test_votes_toggle_once_per_user_and_only_on_published_comments(self):
        comment = self.comment()
        url = reverse("comments:upvote_comment", kwargs={"comment_id": comment.pk})
        self.client.force_login(self.other)

        first = self.client.post(url, HTTP_ACCEPT="application/json").json()
        second = self.client.post(url, HTTP_ACCEPT="application/json").json()
        self.assertEqual(first["score"], 1)
        self.assertTrue(first["voted"])
        self.assertEqual(second["score"], 0)
        self.assertFalse(CommentVote.objects.exists())

        pending = self.comment(status=Comment.Status.PENDING)
        denied = self.client.post(
            reverse("comments:upvote_comment", kwargs={"comment_id": pending.pk}),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(denied.status_code, 400)

    def test_vote_forms_redirect_back_with_a_message_for_browsers(self):
        comment = self.comment()
        self.client.force_login(self.other)

        response = self.client.post(
            reverse("comments:upvote_comment", kwargs={"comment_id": comment.pk})
        )

        self.assertRedirects(
            response,
            f"{self.post.get_absolute_url()}#comment-{comment.pk}",
            fetch_redirect_response=False,
        )

    def test_reports_are_unique_per_user_and_requeue_at_the_threshold(self):
        comment = self.comment()
        url = reverse("comments:report_comment", kwargs={"comment_id": comment.pk})
        users = [
            get_user_model().objects.create_user(
                username=f"reporter-{i}", email=f"reporter-{i}@example.test"
            )
            for i in range(3)
        ]

        self.client.force_login(users[0])
        first = self.client.post(
            url, {"reason": "spam"}, HTTP_ACCEPT="application/json"
        )
        again = self.client.post(
            url, {"reason": "spam"}, HTTP_ACCEPT="application/json"
        )
        self.assertEqual(
            (first.json()["reported"], again.json()["reported"]), (True, False)
        )
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.APPROVED)

        for user in users[1:]:
            self.client.force_login(user)
            last = self.client.post(
                url, {"reason": "abuse"}, HTTP_ACCEPT="application/json"
            )

        self.assertTrue(last.json()["requeued"])
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.PENDING)
        self.assertEqual(CommentReport.objects.filter(comment=comment).count(), 3)
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 0)

    def test_report_rejects_own_pending_and_unknown_reasons_safely(self):
        comment = self.comment()
        own_url = reverse("comments:report_comment", kwargs={"comment_id": comment.pk})

        own = self.client.post(own_url, HTTP_ACCEPT="application/json")
        self.assertEqual(own.status_code, 400)

        self.client.force_login(self.other)
        odd = self.client.post(
            own_url, {"reason": "<script>"}, HTTP_ACCEPT="application/json"
        )
        self.assertEqual(odd.status_code, 200)
        self.assertEqual(CommentReport.objects.get().reason, CommentReport.Reason.OTHER)

    def test_owner_edit_is_rescreened_and_limited_to_the_window(self):
        comment = self.comment("Original wording")
        url = reverse("comments:edit_comment", kwargs={"comment_id": comment.pk})

        clean = self.client.post(
            url, {"body": "Better wording"}, HTTP_ACCEPT="application/json"
        )
        comment.refresh_from_db()
        self.assertEqual((clean.status_code, comment.body), (200, "<p>Better wording</p>"))
        self.assertEqual(comment.status, Comment.Status.APPROVED)
        self.assertEqual(comment.metadata["history"][0]["body"], "Original wording")

        self.client.post(
            url,
            {"body": "buy now click here free money http://x.example"},
            HTTP_ACCEPT="application/json",
        )
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.SPAM)

        short = self.client.post(url, {"body": "x"}, HTTP_ACCEPT="application/json")
        self.assertEqual(short.status_code, 400)

        Comment.objects.filter(pk=comment.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )
        late = self.client.post(
            url, {"body": "Too late now"}, HTTP_ACCEPT="application/json"
        )
        self.assertEqual(late.status_code, 400)

    def test_only_the_owner_may_edit(self):
        comment = self.comment("Original wording")
        self.client.force_login(self.other)

        response = self.client.post(
            reverse("comments:edit_comment", kwargs={"comment_id": comment.pk}),
            {"body": "Hijacked"},
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Original wording")


class ModerationQueueTests(CommentTestCase):
    def setUp(self):
        super().setUp()
        self.moderator = get_user_model().objects.create_user(
            username="cm-mod", email="cm-mod@example.test", is_staff=True
        )
        self.moderator.user_permissions.add(
            Permission.objects.get(codename="change_comment")
        )
        self.queued = self.comment("Needs a human", status=Comment.Status.PENDING)

    def test_queue_page_is_staff_only_and_shows_the_flags(self):
        self.assertNotEqual(
            self.client.get(reverse("comments:moderation_queue")).status_code, 200
        )
        self.client.force_login(self.moderator)

        response = self.client.get(reverse("comments:moderation_queue"))

        self.assertContains(response, "Needs a human")
        self.assertContains(response, "Waiting for review")
        self.assertNotContains(response, "cm-reader@example.test")

    def test_decisions_publish_and_notify_exactly_once(self):
        self.client.force_login(self.moderator)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("comments:moderation_action"),
                {"comment_id": self.queued.pk, "action": "approve", "note": "ok"},
            )

        self.queued.refresh_from_db()
        self.assertEqual(self.queued.status, Comment.Status.APPROVED)
        self.post.refresh_from_db()
        self.assertEqual(self.post.comments_count, 1)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.author, action_type="comment"
            ).count(),
            1,
        )
        self.assertFalse(notify_comment_published(self.queued.pk))
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.author, action_type="comment"
            ).count(),
            1,
        )

    def test_unknown_actions_and_missing_permission_are_rejected(self):
        staff_without_permission = get_user_model().objects.create_user(
            username="cm-noperm", email="cm-noperm@example.test", is_staff=True
        )
        self.client.force_login(staff_without_permission)
        denied = self.client.post(
            reverse("comments:moderation_action"),
            {"comment_id": self.queued.pk, "action": "approve"},
        )
        self.client.force_login(self.moderator)
        bad = self.client.post(
            reverse("comments:moderation_action"),
            {"comment_id": self.queued.pk, "action": "bogus"},
        )

        self.assertEqual((denied.status_code, bad.status_code), (403, 400))
        self.queued.refresh_from_db()
        self.assertEqual(self.queued.status, Comment.Status.PENDING)

    def test_notifications_skip_unpublished_comments_and_self_comments(self):
        mine = self.comment("My own comment", user=self.author)

        self.assertTrue(notify_comment_published(mine.pk))
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.author, action_type="comment"
            ).exists()
        )
        self.assertFalse(notify_comment_published(self.queued.pk))

    def test_rate_limit_helper_counts_per_account_for_other_apps(self):
        from django.test import RequestFactory

        from apps.comments.views import _check_comment_rate_limit

        request = RequestFactory().post("/")
        request.user = self.reader

        results = [_check_comment_rate_limit(request) for _ in range(6)]

        self.assertEqual(results, [True] * 5 + [False])
